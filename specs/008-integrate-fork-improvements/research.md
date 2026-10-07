# Research and selection decisions

## Fuentes
Auditorías docs/audits/2026-10-02-autonomous-orchestration.md y 2026-10-02-fork-branches.md; SHA congelados de32 ramas origin y upstream. Baseline local guardado fuera del repositorio; no depende de HEAD limpio.

## Decisiones
- Integración selectiva de conducta con cobertura completa; alternativa descartada: merge/sustitución de árbol que eliminaría garantías Work/007/browser.
- P1 primero: incertidumbre post-send, restauración atómica y backoff local; no dejar que fallos de diagnóstico gobiernen liveness.
- Índices causales de735 complementan recibos durables, con nombres separados; backstop no certifica resultado.
- Autoría: última revisión upstream de publicación/CAS y continuación fork plan-v2 como fuentes; adaptar a autoridad/Work actuales, no copiar un default de aprobación sin su secuencia de uso.
- Ralph/Beads: conservar flujos de epics/dependencias/feedback mediante evidencia durable Work; no crear un segundo sistema más débil cuya palabra COMPLETE sea prueba final.
- Graph cache: tareas independientes y presupuestos, manteniendo identity/owner/policy keys actuales.
- Apps-only: restricción fail-closed sólo cuando se solicita, registros de HTML por disponibilidad; permisos del backend siguen vigentes.
- Remoto: bridge con identidad/canal/operaciones y routing explícitos, mantener tanto nodos CAO target_host como runtimes de ejecución sin mezclar tokens.
- Nuevas funciones opcionales entregadas con configuración documentada; ninguna activación de infraestructura/cuentas/releases implícita.

## Evidencia específica
Notas por dominio se incorporan antes de sus cambios. Las reproducciones B01-B05 y diferenciales de rama quedan en docs/audits/evidence. No hay decisiones de alcance sin resolver: todos los grupos útiles están incluidos; elecciones de implementación deben conservar escenarios inventariados.
