# T182 · Propiedad de cancelación entre llamadas concurrentes

Validación de composición sin modelo, 2026-10-27. La implementación productiva no cambió: se añadieron pruebas de fronteras que no cubrían juntas los contratos previos. No se reprodujo un defecto del producto.

El nuevo módulo usa el servicio `cancel_turn`, el bloqueo asesor y los recibos/estados de hijo reales de PostgreSQL. Controla únicamente la parada del backend y la disponibilidad del proveedor mediante barreras, sin dormir para inferir exclusión. Los once escenarios verifican:

- Mientras reset o initialize están detenidos: estado cancelling, recibo local conservado, rechazo de resultados tardíos y de nuevas generaciones; el hijo pasa de sent a cancelled después del reset confirmado.
- Desconectar al llamante durante reset o initialize no abandona el trabajador protegido: termina la cancelación y libera su bloqueo.
- Una segunda cancelación recibe realmente False de `pg_try_advisory_lock` mientras el primer propietario está detenido. Tras liberar al primero, adquiere el bloqueo, pierde el CAS y no ejecuta otro reset. Se comprueba también la desconexión del llamante en espera.
- Fallos de reset, initialize=False, excepción de initialize o negativa del CAS final conservan cancelling y el recibo local, bloquean nueva entrada y publican reconciliación; no publican disponibilidad.
- Una generación obsoleta se rechaza antes del backend; después de una cancelación confirmada se permite una generación nueva y el antiguo id ya no puede cancelar el terminal.
- Otro terminal puede completar su cancelación mientras el primero sigue detenido: la exclusión pertenece al terminal, no a un bloqueo global.

La publicación normal ocurre después de cerrar la conexión y liberar la clave del bloqueo de ese trabajador. El test no vuelve a adquirirlo síncronamente desde el evento, ya que otro llamante podría ser su propietario legítimo. No se envía ni prepara de nuevo el prompt.

## Verificación y revisión

Primera versión:10pruebas pasan en18,98s; regresión inicial156PASS144,97s/5avisos. La revisión de Claude pidió una observación más fuerte de la espera del segundo llamante. Se añadió la negativa SQL real, bandera adquirida solo tras retornar el bloqueo y la variante de llamante en espera desconectado. Revisión final:11PASS19,94s/2avisos Pydantic. Los proxies devuelven las filas reales y conservan la conexión/cierre PostgreSQL; no simulan el resultado del bloqueo.

Regresión final del archivo revisado:157PASS157,10s/5avisos de deprecación, sin skips. Incluye los11controles nuevos; no se suman las ejecuciones previas como casos distintos. Los hashes de los12archivos de prueba permanecieron iguales durante la ejecución. Base ejecutada `5558358d`; integración `8a5449c2bffe39fcbe011b23d4acbf1db5d7291d` conserva los mismos bytes productivos y del test revisado.

Black/isort con configuración explícita y diff revisado. Arquitectura5contratos conservados/0rotos. Claude revisó el archivo final y las rutas de servicio/bloqueo en modo read:GO, sin comandos ni ejecución independiente de pruebas. [Recibo estructurado](validation-t182.json).

Graphify apuntó a recover_terminal_turn→cancel_turn→_cancel_serialized→_cancel_locked y los CAS de persistencia. Sus líneas eran antiguas: se contrastaron con el código actual. No se reconstruyó el grafo ajeno porque no cambió la estructura productiva.

## Composición y límites

Se ejercitan servicio→bloqueo asesor→CAS de recibo→backend controlado→hijo persistido→readiness controlada→CAS final→liberación→evento, con PostgreSQL real. Los contratos HTTP/MCP de autoridad, errores y Work se incluyen en la regresión existente; no sustituyen una desconexión HTTP real. Sin cambios de esquema, migraciones, permisos, reintentos o límites productivos.

`supports_event_inbox=True` excluye deliberadamente el ramal FIFO de estos controles. No acredita drenaje del FIFO, origen de un frame nuevo, login/readiness nativos, cancelación activa real de Codex/OpenCode/Claude, timeout, todaCX-08 ni aceptación global. La posible llegada de bytes del pane antiguo sigue siendo una hipótesis que requiere observación nativa; no se corrigió el producto a partir de ella. T032 conserva su aplazamiento.

PostgreSQL privado verificado:0conexiones/0bases privadas antes de cerrar al propietario; contenedor y red exactos ausentes. Memoria previa al aprovisionamiento9814MiB. No se crearon procesos de modelos ni se leyó/modificó el login del operador. El caso tmux existente usa su propio socket y ejecuta su cierre en finally; no se afirma una auditoría global de procesos. Se conservan los registros privados en `/home/felni/.cache/caos-cancellation-ownership-20261027`.

Conclusiones duraderas guardadas en Obsidian mediante una transacción de cuatro páginas (`save-20261027-caos-cancellation-ownership`), lint estricto sin hallazgos.
