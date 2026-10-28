# T191 · admisión estructurada y correlación del turno

**Corrección validada offline. T190 real sigue abierto.** No cambia permisos,
proveedor/modelo, API productiva ni semántica global del monitor.

El POST de entrada debe devolver `success is True` y una secuencia entera
positiva; HTTP exitoso, `False`, booleanos usados como IDs, cadenas y campos
ausentes no acreditan admisión. El consumidor no reenvía una entrada incierta.
Antes del POST lee la generación durable de `/terminals/{id}/turn`; no envía
si no puede vincular esa observación al terminal/proveedor propio. Este estado
sobrevive al reinicio, aunque los contadores del monitor vuelvan a cero.

Durante la fase exige secuencia aceptada y recibo durable verificado de una
generación nueva, distinta de la previa y estable durante la observación. Ni
`completed` almacenado en caché ni el contador de turnos no vistos prueban
finalización del proveedor. El cambio de generación y la reutilización del
recibo anterior se rechazan. El camino positivo conserva filas/eventos/audits
exactos y espera al recibo de ese turno. Los metadatos de como máximo dos fases
se capturan en memoria y se escriben una vez fuera del workspace eliminado;
no guardan cuerpos, instrucciones, recibos ni generaciones en texto.

Se reprodujeron15controles RED iniciales. Dos controles adicionales ejecutaron
el waiter del commit42a55705 en un proceso aislado, sin revertir el checkout:
ambos demostraron aceptación prematura de efectos o de otra generación. Tres
controles RED detectaron la falta de baseline/rechazo antes del POST y la
reutilización de generación tras reinicio. La prueba positiva también detectó
y corrigió una condición mal compuesta del borrador antes de cualquier run
nativo. **230 pruebas combinadas PASS en42,09s**, incluidos24controles nuevos;
black/isort y composición5contratos conservados/0rotos. Revisión independiente
ClaudeGO, de lectura y sin ejecución propia.

El R3 real se conserva como FAILED: estado consultado `completed`,0llamadas,
filas/eventos y cleanup propio completo. No conservó el texto del modelo y no
acredita que ese estado perteneciera al turno enviado. Esta corrección no
establece su causa. [Recibo R3](validation-t190-native-r3.json).

El probe sintético de oferta quedó preparado y cerrado. La sonda de namespace
y preflight estático pasaron sin Codex/MCP/HTTP/modelo, pero su HOME no conserva
el catálogo efectivo de R3; no demuestra sus herramientas ofertadas. Se
abandona esa propuesta como prueba causal y no se amplían permisos por una
hipótesis de `tool_search`. Los gates de oferta nativa permanecen separados.

El próximo recorrido usa un observador privado opcional antes de cleanup:
viewport del terminal propio, limitado, con identidad/secuencia/generación
comprobadas antes/después; nunca se publica el texto ni se llama resultado
verificado del modelo. Cinco controles con HTTP local falso pasan (normal,
catálogo inválido, patrón sensible, exceso de tamaño, cambio de generación),
y la limpieza se ejecuta aun ante excepción/interrupción del observador.
Cero llamadas nativas/modelo/PostgreSQL en ese preflight. Su fallo no sustituye
ningún criterio de aceptación, y el R4 aún no se ha ejecutado.
