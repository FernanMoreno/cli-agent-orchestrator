# T195 · recuperación Claude de una espera nativa activa

**PASS offline y nativo el2026-10-28. CL-09 cerrado; T012 agregado sigue abierto.**

El recorrido de preguntas/ACK/reinicio de Claude ya pasó en f77e3e66, pero no
interrumpía una espera activa. El normalizador existente admite los eventos
Claude assistant.tool_use y user.tool_result bajo el mismo ID. Se extiende
únicamente la admisión explícita de run y CLI a Codex/Claude, ayuda y etiqueta
de error; no se cambia el normalizador, MCP, retry, persistencia ni permisos.

TDD:3RED por restricciones existentes/etiqueta anterior y7controles de stream
pasan antes del cambio; selección final completa de interacción/reinicio:
102PASS en44,49s. Incluye entrada al preflight sin adquirir recursos y eventos
Claude con ID coincidente, grupo/worker incorrecto, timeout no decreciente,
error de herramienta, resultado de otro ID y segunda llamada nativa.

Las barreras conservan una llamada nativa en progreso, entrada del worker API
al mismo grupo, ninguna devolución antes del crash, worker reemplazado, dos
entradas con timeout estrictamente decreciente y una devolución ready. La
aceptación sigue exigiendo ACK con IDs/versiones/recibos exactos, identidad
AG-UI,75s,6144MiB, modelo/coste, aislamiento y limpieza. No se permite <= por
un supuesto redondeo sin reproducción. Un resultado positivo offline no acepta
la recuperación real. Otros proveedores/combinaciones siguen rechazados.

Composición: CLI/run -> normalizador nativo -> ledger de espera API -> reinicio
propietario -> resultado/ACK. Fronteras, autoridad y ownership existentes sin
cambio. Black/isort/diffPASS, arquitectura cinco contratos conservados/cero
rotos y revisión ClaudeGO; Luna confirma el formato Claude. No hay interfaz
productiva nueva que requiera Pact, migración o cambio de base de datos.
Graphify se consultó en lectura (54428nodos/106992aristas), pero no representa
estas fixtures ni sus módulos; límites verificados contra código. No se
regenera el grafo ajeno ni se atribuyen relaciones no observadas.

## Resultado real

[Recibo sin secretos](validation-t195-native.json), fuente
`00d3dac59705d6a48aa0c93dc406ce21d21cbd9c`,505archivos fijados y Claude2.1.303,
modelo solicitado sonnet sin atestación del servido. El adaptador deriva del
revisado y aceptado de reinicio simple: comparación AST verifica que sólo cambia
la raíz privada y restart_during_wait=True. Imports/firmas/context-managers sin
entrar, marcadores frescos, archivo ejecutado0600 y pins comprobados antes de armar.

CLI46,62s; propietario/adaptador/limpieza72,712s; mínimo10063MiB. Una creación de
grupo/dos preguntas y dos respuestas, una llamada nativa wait, una interrupción
del worker API en espera y recuperación en otro worker bajo plazo decreciente.
Una devolución ready y un ACK de las dos respuestas con grupo/IDs/versiones/
recibos exactos. Tres snapshots AG-UI con identidad estable y redacción; salida0.

Fuente/binario/original de credenciales intactos; raíz temporal retirada,
observador cerrado y contenedor/red PostgreSQL propios ausentes. Recursos ajenos
conservados. Copia privada Claude gestionada por el archivador existente, sin
modificar ni renovar el login original. Ejecutado preservado, descriptor operativo
re-cerrado tras verificar los505pins y cierre. La señal textual heurística marca
auth/quota en el stream pese al PASS; no acredita fallos de autenticación/cuota ni
los acepta como escenarios probados. Las llamadas y el ACK completaron sin error.

T195 y CL-09 cerrados en este alcance. T012 sigue abierto por los otros escenarios
matriciales; T006/504 histórico, autorización por operación/todos los perfiles,
concurrencia, Work y gates globales siguen separados. T032 permanece aplazada.
