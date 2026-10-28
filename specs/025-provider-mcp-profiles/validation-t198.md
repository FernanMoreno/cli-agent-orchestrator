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
Graphify no tiene source_location para estas fixtures/proveedor en el grafo
consultado; las conexiones se verifican directamente contra fuente. No se
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
