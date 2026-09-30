# Aceptación local — 2026-09-30

Alcance y estado vigentes: [acceptance-status.md](../acceptance-status.md).
Este checklist registra aceptación ejecutable local; requirements.md conserva
sus revisiones de calidad de requisitos y no certifica implementación.

## Gates completados dentro del alcance acordado

- [x] T023: cinco entradas mock_cli, reinicio y estado durable.
- [x] T044: snapshot/contexto idéntico tras reinicio.
- [x] T079: owners y cinco contratos Import Linter; deuda indirecta documentada.
- [x] T086: Python, Docker real, Web y TUI; resultados por entorno registrados.
- [x] T087: composición local revisada, con límites explícitos.
- [x] T088: diff revisado y roadmap actualizado con límites.
- [x] T089: Graphify consultado/contrastado; refresh completo diferido y documentado.
- [x] T090: verificación y trazabilidad de 25 FR/10 SC.
- [x] T084: matriz autorizada Codex/Claude/OpenCode v1, 18 celdas tmux/Herdr; 0 € adicionales autorizados y cuotas no agotadas.
- [x] T085: candidato independiente autorizado, 12 conflictos resueltos y aceptación por inventario; sin atribuir su merge a main.
- [x] T123–T129: 119 incidencias resueltas; 13.491 casos aprobados por inventario del candidato y dos workflows reales repetidos en main.
- [x] O03, alcance local: mock_cli y las combinaciones autorizadas aceptadas con transporte real.
- [x] Cierre documental local de 001: gates anteriores reconciliados con sus evidencias y fronteras.

## Ampliación posterior aceptada

- [x] Upstream incorporado en el checkout main preservando cambios locales.
- [x] OpenCode v2: 18 combinaciones autorizadas aceptadas; compatibilidad v1 conservada.
- [x] Dos workflows reales Claude tras renovar el login.
- [x] Despliegue personal localhost: bearer, reinicio, provisión Docker y restauración.

[Spec 003](../../003-personal-deployment/completion-evidence.md) registra resultados
y límites. Diez proveedores externos no autorizados siguen sin aceptación;
cuotas no agotadas, disponibilidad condicionada al equipo encendido. No hay
commit, push, release ni merge de historia nuevos.

Evidencia histórica: [completion-evidence.md](../completion-evidence.md),
[omitted-acceptance.md](../omitted-acceptance.md),
[t084-live-matrix.json](../t084-live-matrix.json).
