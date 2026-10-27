# T183 · Cancelación activa nativa de Codex

PASS acotado, 2026-10-27. Fuente ejecutada `49297912e320775e9776c6d8323caa4f7ffa8380`, CLI 0.164.0 fijado, 1742 archivos y siete entradas privadas vinculados por hashes. [Recibo estructurado](validation-t183.json).

El hijo envió una petición HTTP al backend SSE sintético aislado. El stream seguía abierto con heartbeat reciente, estado API processing y la misma generación pending; no se esperó un canario textual. Una generación distinta y bien formada recibió409 en0,152s, sin cambiar pane/proceso, intento, recibo ni stream. La cancelación de la generación actual recibió200/cancelled en26,075s.

Se confirmó salida por pidfd del wrapper anterior y ausencia de sus cinco procesos propios por UID/nacimiento. El mismo terminal tuvo otro intento nativo, pane de reemplazo y compositor nuevo, sin login, progreso o tarea en su segmento actual. Tres snapshots durante3,161s conservaron cero peticiones POST de tarea del backend de reemplazo. Esto es una observación acotada, no una garantía temporal ilimitada de no reenvío.

El hijo quedó cancelled y settled en el mismo join antes de DELETE. Después:404, sello de limpieza y veredicto/settled_at conservados; el padre siguió accesible por API200. Turno cancelado y CLI disponible no significan tarea completada con éxito. Se comprobaron HOME/CODEX_HOME/PATH privados antes del stream y en el reemplazo; sin credenciales del operador, cambios globales de tmux ni nuevas herramientas. Dimensiones220×50, límites originales180s/6144MiB.

## Fallo conservado y corrección

R1 en `ee3567bd` falló su condición HTTP200/≤180; la suite tardó229,78s. El código HTTP exacto no se conservó y no se inventa. El proceso viejo ya había salido y el reemplazo mostraba un compositor nuevo con cero peticiones, pero el recibo pendiente hacía que el detector devolviera PROCESSING. La inicialización esperaba ese mismo detector antes de liberar el recibo: circularidad reproducida con la captura real y el adaptador real. [Corrección T184](validation-t184.md).

R1 conserva su FAIL y su cierre de API/HOME/procesos. El propietario PostgreSQL se mantuvo para regresión y R2. Se usaron inventarios y lanzadores nuevos; los registros del preflight quedaron separados de los gestionados. Un preflight inicial también falló por exigir ausencia inmediata de descendientes tras recoger al padre; se reprodujo sin modelo y se mantuvo el criterio de ausencia con espera acotada. El primer control sin modelo tuvo un supuesto SIGHUP erróneo, corregido en su segunda versión; ambos recibos se conservan.

## Comprobaciones y cierre

20 controles RED del observador;49 enfocados y230 regresiones iniciales pasan. Tras corregir el producto:1116 pruebas pasan/3 omisiones opt-in preexistentes/5 avisos,588,91s. La captura se codificó después en JSON para conservar filas en blanco sin ruido de whitespace; runtime, aserciones y texto decodificado permanecieron iguales, y28 controles enfocados volvieron a pasar en12,28s. No se suman ejecuciones solapadas como una suite distinta. Suite nativa R2:1PASS109,97s. Arquitectura5/0 y mypy canónico de los cuatro archivos modificados sin errores; no es typing global.

API/HOME/socket/procesos propios/sampler cerrados;0conexiones y0bases privadas antes de cerrar PostgreSQL, contenedor y red exactos ausentes. Wrapper y lanzador re-cerrados; fuente, binario e intérprete intactos. Se conservan los cambios ajenos de Graphify. Evidencia privada en `/home/felni/.cache/caos-codex-active-cancel-20261027-r2` y el R1 sin sufijo.

## Límites

CLI y API reales con HTTP sintético solo en loopback. No acredita un modelo servido o cuenta real, cierre TCP final observado por el backend, drenaje universal de FIFO, cancelación nativa con cliente HTTP desconectado, reconciliación de timeout, todoCX-08/CX-09, otros proveedores ni aceptación global. El último ledger del stream viejo puede conservar stream_open=true: es su última observación antes de destruir al propietario, no prueba de un servidor aún vivo. T032 sigue aplazada.

El contador final del backend, comprobado tras terminar el borrower nativo, también quedó en0. La observación cuenta POST de tarea a `/v1/responses`; no es una captura de paquetes ni una auditoría de red de metadatos.

Revisión Claude en modo lectura: GO para código y recibos Markdown, sin ejecución independiente de pruebas ni auditoría de JSON. Codex comprobó directamente cifras, hashes y cierre. Obsidian actualizado mediante `save-20261027-caos-cancel-startup-readiness`, cuatro páginas y lint estricto sin hallazgos.
