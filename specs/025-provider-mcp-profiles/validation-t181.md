# T181 · Codex gestionado con errores envueltos a 80×50

PASS acotado, 2026-10-27. Fuente ejecutada `436dc0ae212df3a9cba88b8932fe459d9f8ea602`, Codex CLI 0.164.0, modelo solicitado gpt-6-luna, 1737 archivos fuente fijados. [Recibo estructurado](validation-t181.json).

| Recorrido | API / tipo / recibo | Tiempo del paso | Celda visible |
|---|---|---:|---:|
| SSE parcial → HTTP 401 | 502 / error / failed | 34,325 s | 2 líneas |
| SSE parcial → 429 usage_limit_reached | 409 / quota_wait / reconcile | 28,690 s | 3 líneas |

La API productiva creó el padre a220×50. Tras vincular id/sesión/ventana/opción `@cao_terminal_id` en el socket privado, se cambió únicamente `default-size` de esa sesión mediante su id inmutable `$N`, antes de crear hijos. El padre quedó a220×50. Ambos hijos arrancaron a80×50 y todas sus peticiones al proveedor conservaron ese tamaño, medido por el backend en fd0:12peticiones HTTP en auth y3en cuota. La configuración productiva de dimensiones no cambió.

Cada respuesta API se vinculó al recibo propio y al mismo hijo en join antes de una única captura visible por caso; se verificaron canario parcial, error nativo envuelto y ausencia de `Worked for`. El validador independiente no usa el parser productivo para acreditar su propio resultado. Después de DELETE:404, mismo veredicto/settled_at más sello de limpieza, pane y wrapper ausentes y padre accesible. Reconcile settled describe la disposición del recibo; no significa éxito ni reanudación automática.

## Comprobaciones y composición

24 controles RED→GREEN del testigo;192enfocadas iniciales y435regresiones finales pasan en52,47s, sin skips. Después de ordenar imports,24controles finales pasan en7,79s; son parte de435, no se suman como casos nuevos. Suite gestionada:1prueba con dos recorridos seriales, PASS116,02s. Black/isort con configuración explícita y diff sin errores. Arquitectura:5contratos conservados/0rotos. Graphify contrastado con create_session/create_window, salida FULL y limpieza del recibo.

La composición comprobó API/run-step→estado nativo→recibo PostgreSQL→join→testigo→DELETE, conservación de identidad/veredicto y continuidad del padre. Sin cambios de esquema, migración, política de permisos ni límites. Se reutilizó un único PostgreSQL privado con identidad verificada para regresión y recorrido. Memoria mínima exigida6144MiB, sin reducir180s ni reintentos. El backend sintético funciona solo en loopback, con HOME del operador oculto y sin cargar su login.

Los preflights nativos finales auth/cuota pasaron con perfil realmente consumido y TTY80×50 desde arranque. La revisión detectó que HTTPServer podía ocultar un OSError de lectura TTY antes del contador; se reprodujo, añadió una marca de error persistente y verificó que el reintento no la borra. Ambos preflights se repitieron con ese guard antes del recorrido API. Las versiones previas y recibos fallidos del arnés se conservan.

Los probes iniciales sin modelo corrigieron supuestos del arnés: un nombre de socket remanente no demuestra servidor vivo; se comprueba identidad/pidfd y ausencia del endpoint del kernel. `show-options -t =nombre` no funciona en este tmux: se obtiene el id inmutable desde el pane propio. También se fijó PYTHONPATH canónico. El probe final acredita herencia220→80 y cierre exacto; estos fallos no se atribuyen al producto. La primera preparación del manifiesto encontró un symlink de fixture a directorio; se aplicó el mismo inventario de archivos regulares de los manifiestos anteriores y se verificó que incluye todos sus pins más los dos archivos nuevos.

Fuentes, binario, intérprete y scripts fijados sin cambios durante el recorrido. Borrower terminado,0conexiones/0bases privadas antes del cierre de PostgreSQL. API/HOME/socket/procesos propios/sampler cerrados; contenedor y red exactos ausentes. Wrapper y lanzador vuelven a estar cerrados. Se preservó el trabajo ajeno de Graphify.

## Límites

Es ejecución nativa real con respuestas HTTP sintéticas y API productiva. No acredita cuota real de una cuenta, raw pipe-pane aislado, dimensiones arbitrarias, prefijos partidos, footer ausente, cancelación activa/timeout, todoCX-08, otros proveedores ni aceptación global. Backend, fuentes ejecutadas, capturas y cierres quedan vinculados por hashes en el recibo; registros privados en `/home/felni/.cache/caos-codex-managed-narrow-20261027`.

Revisión final Claude en modo read:GO, sin hallazgos bloqueantes; inspeccionó el recibo nativo, Markdown y guard/asserts TTY, sin ejecutar pruebas ni auditar todos los pins. Codex verificó directamente hashes, suites y cierre. Obsidian actualizado en una transacción de4páginas (`save-20261027-caos-managed-narrow-errors`), lint estricto0hallazgos.
