# T155 — fila de creación cancelada y ausencia nativa

2026-10-26. FR133/SC117 sobre main `7f7027edb50a02a4a778ab318aa043469066daac`. Retirada de fila aceptada en su alcance acotado. [Fuentes y recibos](validation-t155.json).

## Reproducción y causa

El compensador tardío comprobaba la encarnación antes del intento de parada por etiqueta, pero borraba la fila después aunque omitiera esa parada, devolvieraFalse o lanzara una excepción. Un retornoNone del helper no acredita ausencia nativa. Además, una fila y un puntero nuevos podían coincidir entre sí sin pertenecer al intento cancelado.

Con PostgreSQL, un Git/worktree temporal y un backend controlado con marcador de ventana viva, cuatro casos borraron su fila tras killFalse/excepción con presencia o incertidumbre; cuatro controles de ausencia pasaron. RED:4 fallos,4 correctas,2 avisos,41,11 s; propietario47,023 s cerrado/ausente. Antes de cambiar producción se añadieron puntero cambiado y sustitución de fila con el mismo ID: **6 fallos,4 controles correctos,37,82 s**; propietario43,336 s cerrado/ausente y14 fuentes intactas.

## Prueba de propiedad y retirada

El trabajador captura id/sesión/ventana/proveedor/motor/encarnación después de confirmar el INSERT y antes de publicar WorkerOutcome. El compensador lee ese testigo solo después del resultado exitoso. Exige admisión sesión→dispatch→retirada lógica bajo el contexto de startup, rechazo de Work/WorkBackendView, vinculación coincidente y encarnación actual antes de parar.

La parada por etiqueta y su limpieza del mapa de entorno conservan el helper anterior. Después se exige `cleanup_terminal_exact(..., close=False)` con resultado ABSENT/DELETED, se releen vinculación y puntero y se requiere que delete_terminal_row retorne exactamenteTrue. KillTrue no basta; killFalse puede significar que ya no existe, pero solo la prueba exacta permite borrar. Excepción, presencia, UNKNOWN, cambio de vinculación, falta de autoridad o negativa/error de fila conservan evidencia y no sustituyen la cancelación original.

El booleano privado solo indica retirada de fila. Finisher sigue liberando sus marcadores y startup; no se usa para retirar worktrees tardíos ni se añaden estados persistidos de cancelación/reconcile. T154 conserva el directorio después del envío del backend.

## Pruebas, controles antiguos y revisión

Los10 casos iniciales pasan tras la corrección:41,68 s,2 avisos, propietario47,437 s cerrado/ausente. Se amplían a27 casos con sesiones/ventanas, pruebas exactas desconocidas/presentes/excepción incluso tras killTrue, ausencia tras killFalse, cambios antes/durante probe, filaFalse/error y falta de binding/Work/WorkView. Comprueban filas y worktrees reales, cancelación original y argumentos exactos ID/sesión/ventana/closeFalse fuera de callbacks capturados por producción. La negativa se observa mediante retornoFalse real, release de marcas y fin del startup. El WorkView inicializado usa spies exitosos: omitir la negativa de tipo provocaría borrado y haría fallar el control. No es una creación Work completa.

Primera enfocada169:167 correctas y2 fallos en128,64 s. Dos preparaciones antiguas exitosas carecían de prueba exacta tipada, y ahora sus filas se retenían correctamente. Se añadió ABSENT explícito a esas dos preparaciones, conservando las aserciones de borrado y propiedad. La selección pasa169 en121,99 s,2 avisos; propietario128,153 s cerrado/ausente.

La primera ampliada se interrumpió tras observar tres controles atómicos de cancelación en rojo. Su FakeTmuxBackend no implementaba cleanup_terminal_exact; la ausencia de ese método impedía confirmar parada. Solo el pytest hijo propio recibió SIGINT; el propietario cerró PostgreSQL y comprobó ausencia en226,569 s, fuentes intactas. Es recibo parcial interrumpido, sin aceptación.

Para esos tres controles se usa un subtipo aislado del simulador original: prueba de lectura ABSENT solo con ID no vacío, closeFalse e inventario completo de sesiones/ventanas vacío bajo su lock; en cualquier otro caso UNKNOWN. No se toca el simulador general, los eventos de SQL ni las aserciones de atomicidad/bloqueos. Los27 nuevos más esos3 pasan:30 correctas,55,63 s. Los fallos y recibos originales se conservan.

Claude dio GO acotado y final en lectura; Luna revisó vinculación/locks, corrigió un falso verde de WorkView y verificó la prueba de inventario y los argumentos exactos. Las ejecuciones son de Codex; no se inicia ningún agente/modelo ni se usa login. Git temporal y PostgreSQL son reales, backend/proveedor de ejecución controlados.

Regresión final: **710 correctas,3 deselecciones configuradas,8 avisos,446,26 s**, incluidos los27 casos nuevos y los controles antiguos con prueba exacta. Memoria inicial11.642 MiB frente al mínimo6.144; PostgreSQL con identidad verificada y propietario cerrado/contenedor exacto ausente en462,864 s. Los14 hashes de ejecución, cuatro archivos de prueba y el helper worktree_service adicional permanecieron iguales. Arquitectura: **5 contratos conservados,0 rotos**. Composición: PASS WITH RISKS descritos abajo. Diff final revisado y mapa AST privado actualizado; Graphify canónico no regenerado para preservar12 cambios ajenos. Conocimiento guardado mediante `save-20261026-caos-cancelled-backend-retirement` en `wiki/concepts/Provider reconstruction ownership.md`, `wiki/index.md`, `wiki/log.md` y `wiki/hot.md`; lint estricto0 incidencias.

## Composición y límites

Se revisan publicación/resultado de trabajador, startup, sesión/Work, prueba nativa, caché/sidecars/fila, WorkView y retención de worktree. No cambian API/esquema/eventos/permisos/configuración/migraciones ni se requiere Pact nuevo. AST verifica que helper de parada/env ordinario, espera propietaria, delete_terminal_row y comprobación de encarnación no cambian; create_terminal solo propaga el binding adicional. No hay mutex global, historial, executor nuevo ni cambios de límites.

SQL final sigue siendo por ID bajo guards actuales, no CAS de revisión distribuida. Mutaciones SQL directas/otros procesos después de la última lectura permanecen en T143. La prueba nativa conserva el alcance del backend existente y el intento de parada por etiqueta anterior; no se añade closeTrue ni se promete rollback de sus efectos o del mapa de entorno.

Una fila conservada sin caller no recibe aquí una clasificación persistida nueva de cancelación/reconcile; conservar evidencia no acredita readiness, entrega ni recuperación automática. Recibos hijos y Work mantienen sus contratos existentes. Fallos del trabajador antes de un resultado exitoso y rollback ordinario siguen separados; worktrees tardíos se retienen. No se acredita aceptación nativa actual ni cierre global.
