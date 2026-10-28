# T195 · recuperación Claude de una espera nativa activa

**Implementado y validado offline; aceptación nativa pendiente.**

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

Siguiente: integrar y fijar el adaptador revisado con sólo la opción explícita
restart_during_wait=True y nueva fuente; preflight real de imports/firmas/
context-managers y markers, ejecutar un caso nativo serial y conservar el
resultado exacto. CL-09/T012 permanecen abiertos hasta sus gates reales.
