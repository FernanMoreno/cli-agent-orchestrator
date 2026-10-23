# Evaluación de código: Búsqueda, filtros y navegación con contexto real

Fecha: 2026-10-23. Base de inspección: `f2c381ed87fab87631a363097da50898de96cd06` más los cambios documentales preexistentes de 025, preservados. SHA de la maqueta: `915b0110d1d775ad738d35bee881d9480d5f2b59cc787bc1d067c7bf9f604690`.

## Estado observado

La maqueta tiene búsqueda local, filtros, breadcrumbs y modos grafo/lista; StudioNav ya agrupa destinos y muestra contexto, pero no prueba búsqueda federada ni restauración completa del grafo aprobado.

## Fuente verificada

- **E01** — [specs/019-frontend-studio/mockup-studio-original.html:434](../../specs/019-frontend-studio/mockup-studio-original.html#L434): Modelo de rangos y estados sintéticos.
- **E19** — [web/src/components/StudioNav.tsx:46](../../web/src/components/StudioNav.tsx#L46): Navegación agrupada existente.

Las líneas se resolvieron sobre los archivos actuales. Los hashes auditados están en [source-evidence.json](../026-studio-graph-conformance/source-evidence.json). Graphify es un mapa previo y sus líneas no sustituyen esta inspección.

## Brecha y propiedad

Concreta navegación pendiente de 019; no cambia la autoridad o la organización al filtrar.

No se afirma inexistencia de toda capacidad equivalente: las fuentes citadas acreditan el alcance descrito; la ausencia de integración completa se confirma sobre el mapa y la maqueta inspeccionados. Antes de implementar se revalidarán contratos y recibos recientes para conservar trabajo ya integrado.

## Evidencia exigida para cierre

- Pruebas de contrato e integración de los requisitos FR y escenarios de spec.md.
- Comparación entre registros de origen y presentación, con identidades y revisiones independientes.
- Recorrido real para las operaciones o proveedores que esta entrega declare aceptados; mocks y diagnósticos separados.
- Fallos parciales, duplicados, autorización y recuperación aplicables. Los recibos históricos de otra revisión no se promueven a aceptación nueva.
