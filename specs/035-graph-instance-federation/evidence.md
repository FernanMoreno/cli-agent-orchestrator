# Evaluación de código: Instancias reales y red distribuida observable

Fecha: 2026-10-23. Base de inspección: `f2c381ed87fab87631a363097da50898de96cd06` más los cambios documentales preexistentes de 025, preservados. SHA de la maqueta: `915b0110d1d775ad738d35bee881d9480d5f2b59cc787bc1d067c7bf9f604690`.

## Estado observado

Existen NodeSwitcher, registro local por host/UID/instancia/generación, FleetClient y asignación elástica; ninguno demuestra por sí solo una instantánea federada completa del grafo.

## Fuente verificada

- **E13** — [src/cli_agent_orchestrator/services/local_peer_registry.py:58](../../src/cli_agent_orchestrator/services/local_peer_registry.py#L58): Registro local de procesos y leases, no equipos organizativos.
- **E14** — [src/cli_agent_orchestrator/utils/fleet.py:49](../../src/cli_agent_orchestrator/utils/fleet.py#L49): Inventario de workers y broker, no red lógica ilimitada.
- **E23** — [web/src/components/NodeSwitcher.tsx:25](../../web/src/components/NodeSwitcher.tsx#L25): Selección de nodo existente.

Las líneas se resolvieron sobre los archivos actuales. Los hashes auditados están en [source-evidence.json](../026-studio-graph-conformance/source-evidence.json). Graphify es un mapa previo y sus líneas no sustituyen esta inspección.

## Brecha y propiedad

Refina la observabilidad de 018 sin declararla implementada ni equiparar el registro local a descubrimiento remoto ilimitado.

No se afirma inexistencia de toda capacidad equivalente: las fuentes citadas acreditan el alcance descrito; la ausencia de integración completa se confirma sobre el mapa y la maqueta inspeccionados. Antes de implementar se revalidarán contratos y recibos recientes para conservar trabajo ya integrado.

## Evidencia exigida para cierre

- Pruebas de contrato e integración de los requisitos FR y escenarios de spec.md.
- Comparación entre registros de origen y presentación, con identidades y revisiones independientes.
- Recorrido real para las operaciones o proveedores que esta entrega declare aceptados; mocks y diagnósticos separados.
- Fallos parciales, duplicados, autorización y recuperación aplicables. Los recibos históricos de otra revisión no se promueven a aceptación nueva.
