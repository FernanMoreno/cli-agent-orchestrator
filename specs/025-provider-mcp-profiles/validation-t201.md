# T201 · EOF y señales en el SDK stdio real

FastMCP3.2.0/MCP1.28.1/AnyIO4.15.1, intérprete/entry/metadata y módulos de
transporte fijados. SDK real con0herramientas, observer/sello originales,
initialize/initialized/ping antes de cada estímulo. Sin Claude/auth/API/PG/modelo.
Son controles offline de transporte, no aceptación de proveedor o permisos.

- EOF: retorno normal/exit0/sello válido en0,162s; sin limpieza forzada.
- SIGINT con stdin abierto: no termina ni sella en6s; limpieza posterior SIGTERM,
  returncode-15 pertenece a esa limpieza, no prueba salida inicial por SIGINT.
- SIGINT→SIGTERM100ms: exit-15 en0,1103s, sin retorno normal/sello; sin señal
  adicional de limpieza. El padre propietario acredita esta terminación real.
- SIGINT→EOF100ms, con hijo aún vivo al cerrar stdin: KeyboardInterrupt,
  exit-2 en0,3434s, sin sello; sin limpieza forzada. Es un cuarto caso nuevo,
  sin repetir los tres primeros. El EOF tardío desbloquea salida excepcional,
  no convierte la interrupción en retorno normal.

[Resultados](validation-t201.json). Se conserva6s y se distingue cleanup de
observación; el padre nunca escribe el sello. Todos los hijos directos fueron
reaped con wait y sus lectores terminaron; no se afirma inventario de grupos de
procesos no observado. Archivos ejecutados/SDK fijados sin cambios y ambos
descriptores recerrados.20imports/3firmas/observer real restaurado/sin sello
para servidor nunca ejecutado pasan en preflight. No es un snapshot completo
del entorno: se fijan transportes relevantes/metadata/entry y source helpers.

Revisión ClaudeGO para el control, con límites no disparados en estos casos:
los errores de cierre del observer podrían ocultar la excepción, un fallo de
caso podría faltar en el agregado, readline limita después de leer y no se
inventarían descendientes por wait del hijo. No hubo error parcial/overflow,
los4casos inicializaron/respondieron ping y el cuarto retuvo KeyboardInterrupt.
El close del observer es idempotente en la fuente actual. No se reescriben
recibos para añadir garantías no observadas.

T201 cerrado como diagnóstico. La secuencia nativa T200 de señales es compatible
con esta frontera, pero no se reconstruye el wait status del MCP de T197r2.
T197 sigue abierto: no se puede sellar una señal como EOF normal. La siguiente
corrección evaluará EOF quiescente explícito antes de exit, con el mismo plazo
y conteos, distinguiéndolo de shutdown automático del cliente.
