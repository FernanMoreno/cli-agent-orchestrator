# T085: preparación upstream — 2026-09-30

Estado vigente: T085 completada para preparación/aceptación del candidato aislado;
12 conflictos resueltos y 13.491 casos aprobados por inventario.
[Estado vigente y límites](acceptance-status.md).
La aceptación Python completa por inventario está validada en el clon independiente.
El inventario/simulación que sigue es evidencia histórica previa.
[Revisión vigente](t085-integration-review.md). No se incorporó el merge a main.

## Objetos examinados

- Fork HEAD y origin/main: `0d1b4ff9cf9caa88d28f16a1c9f54c22678813f4`.
- Upstream local, sin refrescar: `2fcc3efa6c6e70039e5b9a7308ee67cd1f024885`.
- Base común: `0ef89f358fa2b475b0025ccb86f8ad20bbadca38`.

Comando: `git merge-tree --write-tree HEAD upstream/main`; exit 1.
Es una simulación sobre commits locales. Escribe objetos Git, pero no hace merge
ni incorpora los cambios pendientes del checkout. Detectó 12 conflictos:

| Archivo | Resolución que debe conservar ambos contratos |
|---|---|
| API main.py | autoridad Work, receipt/result y handoff durable upstream |
| clients/database.py | ledger Work v39 y migraciones aditivas upstream; sin renumerar historial |
| graph/cache.py | identidad, invalidez de cache y fuente vault |
| graph/providers/memory.py | permisos de lectura y proyección vault |
| mcp_server/server.py | identidad autenticada, Work y handoff upstream |
| services/agent_step.py | intento/fingerprint y recibo durable upstream |
| services/cleanup_service.py | cleanup verificado; conservar incertidumbre |
| services/config_service.py | precedencia/versiones y nuevas opciones vault/plugin |
| services/memory_gateway.py | política común de acceso y fuente vault |
| services/memory_relationship_service.py | ACL/CAS y referencias vault |
| services/memory_service.py | ámbito, revisiones y productores vault |
| tui/src/catalog.rs | catálogo del fork y comandos upstream |

La worktree actual solapa además seis paths con upstream: docs/configuration.md,
API main.py, CLI launch.py, clients/database.py, MCP server.py y terminal_service.py.
No hay colisiones por nombre con archivos untracked. Son datos de esta preparación,
no garantía de ausencia de conflicto semántico.

## Secuencia de preparación inicial (histórico, ya ejecutada)

1. Copiar HEAD y cambios locales, incluidos archivos nuevos, a un entorno aislado.
2. Integrar sólo el objeto upstream indicado; conservar ambos lados de los contratos.
3. Probar DB/migraciones/vault, handoff API/MCP, plugins/settings/proveedores,
   memoria/graph, FIFO/estado, Web y TUI; ejecutar composición y revisar diff.
4. Entregar el candidato y sus resultados para revisión. La rama main permanece
   intacta hasta una solicitud explícita de integración.

En la fase de inventario/simulación T085 permanecía sin marcar. La preparación
y aceptación posteriores la completaron; su incorporación a main es una acción
separada. La simulación inicial, por sí sola, no acreditó integración.


## Corrección posterior de aceptación — 2026-09-30

Las 119 incidencias del resultado anterior están resueltas en el candidato:
**13.491 passed por inventario**, con 15 regresiones nuevas y aceptación real
por entorno. Véase [omitted-acceptance.md](omitted-acceptance.md) y su JSON por nodeid.
Los contadores antiguos arriba describen ejecuciones históricas.
