# Estado vigente de aceptación — 2026-09-30

**Cierre documental del alcance local acordado.** Spec 001 tiene 129 tareas
completadas; spec 002 tiene 7. Este cierre acredita los escenarios y entornos
probados. El despliegue personal posterior se acepta en este equipo; no certifica
funcionamiento universal ni disponibilidad con el equipo apagado.

## Gates del cierre local

| Gate | Estado vigente | Evidencia y alcance |
|---|---|---|
| 001: implementación local | Completada | [tasks.md](tasks.md), [completion-evidence.md](completion-evidence.md) |
| 002: normalización LF | Completada | [tareas 002](../002-normalize-line-endings/tasks.md) |
| T084: matriz autorizada | Aceptada | Matriz histórica v1 y aceptación posterior v2: 18 combinaciones tmux/Herdr; [evidencia 003](../003-personal-deployment/acceptance-evidence.json); cuota not_applicable |
| T085: upstream | Incorporado en el checkout main | Candidato aceptado y delta integrado preservando cambios locales; 505 rutas aplicadas, 25 idénticas, 13 conflictos revisados; HEAD e historia sin cambios |
| T123–T129: omisiones y fallo conocido | Completadas | 119/119 incidencias resueltas; [informe](omitted-acceptance.md) y [detalle por nodeid](omitted-acceptance.json) |
| Inventario Python del candidato | 13.491 aprobadas | Resultados conservados y repeticiones por entorno; no una única ejecución de pytest ni la cobertura completa de main |
| Docker y kernel local | Aceptados en sus entornos | T019/T020 y setup Docker reales; QEMU Linux 7.3/Landlock ABI 11, T097 8/8 con broker y timeout normal |
| Workflows reales en main | Aceptados | Dos workflows Codex históricos; dos workflows Claude posteriores al login renovado, 2 passed en 173,15 s |
| Arquitectura y paquete del candidato | Verificados | 5 contratos conservados/0 rotos; wheel instalada, Web, MCP Apps y TUI; [informe](omitted-acceptance.md) |

## Ampliación aceptada: spec 003

[Las tareas de 003](../003-personal-deployment/tasks.md) completan la integración
del candidato en el checkout main, OpenCode v2 conservando v1, workflows reales
Claude tras renovar el login y el despliegue personal persistente y autenticado.
La web requiere acceso autenticado mediante el comando `web`; su corrección
posterior al informe Offline está aceptada con navegador real.
CAO escucha en localhost bajo systemd; reinicio automático y dos restauraciones
reales preservaron estado y recibos. Docker Work exige provisión explícita y una
imagen inmutable. El lanzamiento estático fue admitido y limpiado; no se presenta
como resultado de un modelo dentro de Docker.

La matriz v2 acepta 18 combinaciones: nueve Herdr, ocho tmux conservadas y la celda
Claude→OpenCode corregida y aprobada dos veces. No fue una sola ejecución verde.
[Informe de aceptación](../003-personal-deployment/completion-evidence.md) y
[guía operativa](../../docs/personal-deployment.md).

## Límites y acciones separadas

- **O03 ampliado:** el inventario registra 14 proveedores. Tres externos están
  autorizados y aceptados en tmux/Herdr; mock_cli tiene aceptación local. Los diez
  externos restantes carecen de cuenta/modelo autorizados y siguen sin aceptar.
- **Publicación:** incorporación de código en main no implica commit, push,
  release ni merge de historia Git. Ninguna de esas acciones se hizo en 003.
- **Disponibilidad:** el despliegue es para el usuario desde este equipo; requiere
  que el equipo y WSL sigan funcionando. No acredita un servicio remoto 24/7.
- **Pruebas:** las 13.491 aprobadas pertenecen al inventario del candidato anterior.
  Los gates actuales de main están separados en 003; no se afirma haber repetido
  toda la suite del candidato en main.
- **Cuotas:** no se provocó agotamiento. Presupuesto: 0 € adicionales, suscripciones
  existentes y modelo Go explícitamente gratuito. Work conserva autoridad finita.

## Lectura de la documentación

Este documento resume el estado vigente. [El checklist local](checklists/local-acceptance.md)
registra el cierre dentro de ese alcance. Las entradas fechadas en
[workflow-status.md](workflow-status.md), los resultados anteriores y las
simulaciones Git se conservan como historia; no sustituyen este estado vigente.
El cierre documental local no es un signoff de release ni del programa ampliado.
