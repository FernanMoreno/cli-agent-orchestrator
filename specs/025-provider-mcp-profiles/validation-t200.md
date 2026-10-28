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


## Control real r1

Fuente `d48420da5d7916086c27c62e7271c2f72a4609a6`,507bindings. Trigger suprimido antes de preguntas/
mensajes:0MCPcalls/0filas/0eventos, sin reinicio. Metadata inicialIDLE; identidades
CLI y MCP presentes/estadoS antes de exit. Tras el helper de6s y antes de DELETE,
ambas identidades y directorios /proc ausentes, pane otra vez en shell, sin sello.
HTTP200 de exit/no_seal_at_poll_return/exhausted_after_poll. La CLI sí salió:
no es sólo la rama de omisión WAITING ni un MCP vivo esperando indefinidamente.
Todavía no se distingue terminación por señal de excepción/fracaso de cierre.

Controlador85,112s, mínimo9589MiB;26identidades observadas retiradas, API/tmux/
observador detenidos antes de cerrar PG, contenedor/red exactos ausentes,
otros contenedores y login intactos, staging retirado.507pins y archivo
inmutables; descriptor recerrado. [Recibo](validation-t200-native-r1.json).
El accepted=false del recorrido se conserva: la supresión intencional no acepta
mensajes ni convierte ausencia de sello en cierre correcto.

Siguiente observación r2: --debug-file verificado con el --help del binario
instalado2.1.303, que habilita debug implícitamente. Un wrapper sólo del API añade
la ruta de log nativo precreada0600 bajo raíz privada a cada comando original.
MCP/observer/PYTHONPATH permanecen iguales; no se repiten mensajes. El log puede
contener secretos: permanece privado y sólo se extraerán códigos/señales fijos
tras revisar la procedencia. No hay strace instalado y no se reinstala nada.
12grupos/30imports PASS incluyen el Popen real/closure y constructor de debug/
argumentos/0600/exclusividad/id inválido. El diagnóstico r2 sigue cerrado a la
espera de revisión; sin cambio productivo ni atribución retrospectiva de señal.


Revisión r2 ClaudeGO. Se vincula además el inode/device/modo del log al fd
precreado, antes de lanzar el CLI, para verificar identidad y0600 al extraer.
El control del constructor real comprueba ese binding. Los archivos debug no
se publican; sólo se admitirán eventos fijos/código/señal con contexto MCP
reconocido. Un log sin evento de cierre seguirá siendo no concluyente.


## Control real r2 · señales de cierre registradas por el cliente

Fuente `18f4a48b9e44abb4aca10efea95fdb78de613957`,507bindings. Registro nativo en archivo0600 con
inode/device originales verificados, helper/API ligados por hash. Se repiten
metadataIDLE, CLI/MCP presentes antes y ausentes antes de DELETE, sin sello.
El cliente registra SIGINT,100ms después escalado a SIGTERM y51ms después
salida reportada como limpia. El log no da código de salida ni estado waitpid:
son intenciones/eventos del cliente, no prueba de que SIGTERM mató al hijo ni
reconstrucción de la señal exacta de T197r2. El sello normal sigue fallido.
Sólo se exportan enums/señales/offsets del log y números de línea reconocidos;
el log crudo y su hash quedan privados. Los offsets no sustituyen el deadline
monotónico del helper de6s.

Controlador74,913s, mínimo9651MiB;0triggers/0MCPcalls/0filas/eventos/reinicios.
No es telemetría externa del backend.25identidades observadas retiradas, API/
tmux/observador detenidos antes de cerrar PG, contenedor/red exactos ausentes,
originales preservados y staging auth retirado.507pins/archivo ejecutado/helper
verificados y descriptor recerrado. [Recibo](validation-t200-native-r2.json).

T200 cierra su observación acotada de salida nativa. T197 permanece abierto,
sin aumentar6s ni convertir señales en sello normal. T201 contrastará el SDK
stdio real con EOF/SIGINT/escalado sin Claude/modelo/auth/PG para observar la
salida de FastMCP.run directamente antes de elegir una corrección.
