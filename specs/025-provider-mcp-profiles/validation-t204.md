# T204 · Exclusión nativa del cliente Claude: verificadores y preflight

Implementados el reductor tipado, la fixture Anthropic SSE local y un proxy
MCP transparente con contador correlacionado. Ninguno modifica permisos del
producto ni copia credenciales. Se mantiene intacto el lector de catálogo T188.

El contrato exige catálogo servidor con la herramienta, oferta HTTP sin ella y
con las permitidas exactas, flag cliente explícito y ausencia de filtro servidor,
tool_use/tool_result nativos únicos/ordenados/correlacionados, is_error booleano,
seguimiento HTTP validado y ningún tools/call. Una captura intermedia del proxy
no basta: closed_cleanly sólo aparece tras cierre normal, hijo0 y sin RPC pendiente.
El backend primario se identifica por nonce y oferta, nunca por ordinal; las
peticiones auxiliares no consumen la inyección. Límites de entrada y número de
peticiones explícitos, sin registros de argumentos en el contador.

TDD: colección RED por módulo ausente;27pruebas iniciales PASS. Selección final
74PASS/14,06s (contrato/fixture/proxy y regresión del catálogo). Incluye HTTP real
loopback y tuberías/proceso proxy con initialize/list/call reales contra un hijo
de control, conteo1 y cierre normal. Esto no ejecuta Claude ni acredita permisos
nativos. Se conserva el fallo intermedio de expectativa sin closed_cleanly.

Revisión Claude: detectó ambigüedad cliente/servidor, captura intermedia y backend
no ligado al reductor; corregidos y probados. GO final para integrar helpers.
Lanzador privado aún sin aceptación: necesita controlar carrera de apagado,
retirada de descendientes y entorno sin auth. El preflight real del builder
rechazó un proxy declarado como servidor canónico; corregido para envolver el
comando después de generar la configuración.11imports y construcción real del
argv/env comprobados sin CLI/login/listener.

Composición:5contratos conservados/0rotos. Graphify se consultó sólo en lectura;
ubicó builder/resolución config, con líneas antiguas contrastadas en fuente. No
se regeneró ni incluyó el trabajo Graphify ajeno. Fronteras revisadas: producción
config→CLI, SSE→eventos nativos, stdio correlacionado→contador/cierre; sin cambios
de API, persistencia, eventos de producto o migraciones.

T204 y CL07 permanecen abiertos hasta el control nativo específico. El siguiente
paso es catálogo/apagado nativo sin turno, dentro del namespace sin red externa,
con HOME/config vacíos, clave dummy y máscaras de login comprobadas. No se toma
prestado PostgreSQL ni se reejecutan casos aceptados o gates aplazados.
