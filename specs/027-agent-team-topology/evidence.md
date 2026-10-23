# Evaluación de código: Equipos, subequipos y pertenencia reales

Fecha: 2026-10-23. Base de inspección: `f2c381ed87fab87631a363097da50898de96cd06` más los cambios documentales preexistentes de 025, preservados. SHA de la maqueta: `915b0110d1d775ad738d35bee881d9480d5f2b59cc787bc1d067c7bf9f604690`.

## Estado observado

CaosScale.children divide rangos de índices y elige al primer agente como coordinador. Work tiene jerarquía de trabajo y proyecto; el registro local identifica procesos y leases. Ninguna de esas fuentes equivale por sí sola a equipos operativos persistentes.

## Fuente verificada

- **E01** — [specs/019-frontend-studio/mockup-studio-original.html:434](../../specs/019-frontend-studio/mockup-studio-original.html#L434): Modelo de rangos y estados sintéticos.
- **E10** — [src/cli_agent_orchestrator/models/work.py:96](../../src/cli_agent_orchestrator/models/work.py#L96): Tarea y jerarquía de trabajo distintas de intento y proceso.
- **E13** — [src/cli_agent_orchestrator/services/local_peer_registry.py:58](../../src/cli_agent_orchestrator/services/local_peer_registry.py#L58): Registro local de procesos y leases, no equipos organizativos.

Las líneas se resolvieron sobre los archivos actuales. Los hashes auditados están en [source-evidence.json](../026-studio-graph-conformance/source-evidence.json). Graphify es un mapa previo y sus líneas no sustituyen esta inspección.

`Terminal.group` ya persiste una lista ordenada de cadenas con búsqueda por prefijo; su semántica pertenece al consumidor. No se debe crear otra fuente redundante ni reinterpretarla silenciosamente como equipos. [Contrato actual](../../src/cli_agent_orchestrator/models/terminal.py#L138). El diseño detallado decidirá reutilización o adaptación con precedencia y migración explícitas.

## Brecha y propiedad

Propietaria de contención y pertenencias. No impone una jerarquía de mando por apariencia ni sustituye sesiones, workers, worktrees o proyectos.

No se afirma inexistencia de toda capacidad equivalente: las fuentes citadas acreditan el alcance descrito; la ausencia de integración completa se confirma sobre el mapa y la maqueta inspeccionados. Antes de implementar se revalidarán contratos y recibos recientes para conservar trabajo ya integrado.

## Evidencia exigida para cierre

- Pruebas de contrato e integración de los requisitos FR y escenarios de spec.md.
- Comparación entre registros de origen y presentación, con identidades y revisiones independientes.
- Recorrido real para las operaciones o proveedores que esta entrega declare aceptados; mocks y diagnósticos separados.
- Fallos parciales, duplicados, autorización y recuperación aplicables. Los recibos históricos de otra revisión no se promueven a aceptación nueva.
