# T200 · cierre nativo sin repetir mensajes

Preparación privada; diagnóstico real pendiente. T197r2 conserva dos llamadas,
filas y eventos correctos, pero la solicitud HTTP200 de salida no produjo sello
normal dentro de6s. No se atribuye ese resultado a una señal concreta.

Se reutiliza el recorrido actual hasta sender/MCP ready y se interrumpe antes
del trigger de pregunta/mensaje. El cleanup original conserva su POST/6s/poll/
DELETE y todos los criterios. El observador envuelve el helper original y guarda
viewport antes/después, antes de DELETE. Valida terminal id/session/name vía
API propia y socket único bajo el tmpdir privado; usa sólo capture-pane y
display-message. La lectura metadata puede refrescar status: el control queda
acotado al sender inicial listo, no idéntico al cierre tras dos mensajes.

La identidad MCP procede del witness de proceso real con UID/birth. La identidad
CLI se busca sólo en su ascendencia, verificando ejecutable nativo/cwd propio y
UID/birth; pane_pid pertenece a la shell y no se confunde con Claude. Si falta
esa observación se registra como no observada. /proc presente sin identidad
legible no se interpreta como salida física. Capturas privadas0600/exclusive/
no-follow; no se publican argv/env, logs de auth ni pantalla cruda. El helper
original conserva valores de retorno y excepciones aunque el observador falle.

10grupos de controles pasan: binding metadata/socket/identidad,2roundtrips,
comandos pasivos,3targets inválidos, PID cambiado, socket ambiguo, symlink,
returnTrue/False/excepción y supresión real del trigger.18imports actuales
comprobados y context-manager PG sin entrar; no servicios/auth/native. Esto
prueba cero triggers de preguntas/mensajes, no telemetría externa de llamadas
al backend si la propia CLI interpretara /exit como un turno.

Fuente/Graphify: exit_terminal_cli -> send_input/send_special_key presentes en
source_file con offsets antiguos; se verifica la fuente actual. HTTP200 también
puede responder una rama WAITING sin envío: no demuestra por sí solo entrega,
consumo de comando, salida CLI, EOF MCP ni retorno normal de FastMCP. El sello
sólo acredita el retorno normal observado; no se sintetiza ni se acepta un
sello posterior al presupuesto como verde. Sin cambios productivos/DB/esquema;
Pact nuevo no aplica al observador privado. T200/T197 siguen abiertos.


Preparación final: ClaudeGO, arquitectura5-0 y controles repetidos en los bytes
finales. Se conserva pre_exit_metadata_status como dato inicial del binding;
no es un estado nuevo consultado después de salir. El recibo del recorrido
seguirá sin aceptar mensajes (trigger suprimido); el informe externo distinguirá
observación completada, sello normal dentro de6s y aceptación de mensajes.
