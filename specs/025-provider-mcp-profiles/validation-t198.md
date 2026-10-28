# T198 · captura de arranque anterior a compensación

Diagnóstico preparado; no acepta mensajes ni cambia producción.
Derivado del controlador T197 con el mismo propietario PG, aislamiento,
login original intacto, límites y retiro de procesos. El wrapper sólo cambia
el entrypoint del API propio: instala un observador de initialize y ejecuta
el mismo módulo API. Los hijos MCP conservan argv/PYTHONPATH; no sitecustomize.
En finally, antes de devolver/lanzar al caller, guarda el viewport visible
privado0600. Observa retornos del probe original sin pedir nuevos estados.
Un subshell command-v no invoca Claude ni cambia PATH/umask de la CLI.
El POST del receptor detiene el control antes de crear emisor/enviar mensajes.

La revisión previa encontró que sustituir globalmente Popen por una función
rompía el isinstance de _api_process, incluida su ruta de limpieza. Se conserva
la versión rechazada sin activar; se reproduce esa frontera offline y se usa
una subclase que mantiene tipo/closure reales. También se rechaza evidencia
previa y se impide que errores del registrador de estados cambien el probe.
No hubo ejecución nativa de la versión rechazada.

Composición: controlador -> entrada API -> proveedor -> viewport/tmux ->
compensación -> retiro de borrowers -> cierre PG. Sin nueva API/esquema/DB o
migración; Pact no aplica a este observador privado. Arquitectura5-0 PASS.
La primera consulta Graphify buscó la ruta en source_location, que sólo guarda
líneas; fue una consulta incorrecta. Consultando source_file se encuentran
initialize/handler/probe y sus aristas a backend/settings/readiness; no las
fixtures de mensajes. Las líneas generadas están desplazadas respecto a fuente
actual, por lo que se verifican directamente las conexiones contra el código. No se
regenera ni modifica el trabajo ajeno de Graphify. La evidencia cruda queda
privada; se publicarán solamente conclusiones revisadas y recibo permitido.

Preflight final:15controles PASS y32imports reales ejecutados sin adquirir
servicios ni staging auth; propietario context-manager comprobado sin entrar.
La reproducción del defecto de tipo y su control corregido usan un proceso
real /bin/true y la función productora _api_process, no sólo un doble de argv.
La instalación sobre el módulo proveedor real también pasó sin arrancar API;
se repetirá su binding sobre los bytes finales antes de activar. Los límites
de6144MiB y tiempos de arranque originales permanecen intactos.

Revisión Claude final GO tras corregir los bloqueos, sobre los bytes finales.
El lead verifica hashes/preflight: el revisor read no ejecuta comandos.
Sin nueva conclusión durable de causa todavía; el resultado y los artefactos
permanecen en el repositorio/recibos privados. No hace falta regenerar Graphify.
Veredicto de preparación: PASS; diagnóstico real y aceptación pendientes.


## Resultado real · 2026-10-28

Fuente7f1e5eeb7fe4633701fd7ae862a8cb4aade64493/505bindings. HTTP500
provider_init_timeout reproducido en146577ms; initialize146,233s,
controlador184,431s, mínimo8850MiB. Binario resuelto en el pane coincide con
el fijado2.1.303. Se capturó el viewport antes de compensación: pantalla inicial
informativa «Security notes» y «Press Enter to continue…»;20probes originales
UNKNOWN. El handler contempla estilo/rutaOAuth/trust/bypass/upgrade pero no
esta pantalla. Esa frontera concreta explica el bloqueo reproducido actual;
no prueba retrospectivamente la pantalla del r1 de T197, que no fue retenida.
El texto «code you trust» es parte del aviso informativo, no el selector de
confianza del workspace ni un formulario de autenticación.

No hubo emisor, mensajes/MCP/filas/eventos/reinicio.15identidades observadas
retiradas, API/tmux/observador detenidos antes de cerrar PG, contenedor/red
exactos ausentes y contenedores originales preservados. Source/native/original
login intactos; staging auth retirado, archivo ejecutado conservado y descriptor
recerrado a su hash cerrado. El viewport crudo permanece privado0600. El campo
private_observation tardío sigue diciendo viewport_saved=false: describe otro
observador posterior, no invalida el startup.viewport_saved=true previo.

T198 diagnóstico completado; T197/T011 siguen abiertos. T199 corregirá este
caso con reconocimiento estricto de viewport actual, nunca Enter genérico ni
aceptación de un banner como readiness. [Recibo](validation-t198-native.json).
