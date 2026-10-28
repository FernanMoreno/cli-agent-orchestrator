# T196 · rechazos estructurados sin falsos positivos por IDs/eventos

**Corregido y validado offline**, sin repetir modelos ni cambiar la aceptación
nativa. Los recibos reales anteriores permanecen inmutables.

T195 había completado ACK/reinicio/limpieza y aun así contenía señales auth/cuota.
La fuente buscaba401 en todo el log y rate_limit_event sin mirar el estado.
Doce regresiones RED reproducen etiquetas incorrectas en IDs opacos, eventos
informativos, metadata inválida y campos de tipo incorrecto; cuatro controles
ya pasaban. Eso demuestra el defecto del clasificador, pero no reconstruye la
coincidencia textual exacta de cada intento histórico cuyo raw fue retirado.

El helper separa los eventos nativos informativos del texto descriptivo: sólo
rate_limit_info.status=rejected aporta la señal de rechazo; allowed,
allowed_warning y estados no probados no la aportan. Un result con is_error
exactamenteTrue y api_error_status entero401 aporta auth. HTTP401 explícito
sigue reconocido en líneas noJSON; las palabras descriptivas heredadas de
errores auth/cuota se conservan. La categoría de fallo sinACK comparte las mismas
señales y mantiene prioridad del timeout.

La [definición oficial de tipos del SDK Claude](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/types.py)
distingue esos estados informativos/de rechazo y los campos de error del
resultado. Son metadata del protocolo, no requisitos de JSON para el modelo.
No se modifica el parser de estado productivo, autorizaciones, modelo, cuerpo
ACK/recibo, presupuesto ni criterio de éxito. Tampoco se considera una señal
heurística como autorización o prueba de un fallo histórico.

Verificación:125PASS de interacción/reinicio (102previos+23controles nuevos),
más19PASS/104deselecciones por selección explícita del consumidor OpenCode,
37,62s y dos avisos preexistentes;144pruebas distintas en total. Los125 tardan
44,69s. Se conservan las opciones de cobertura. Black/isort/diffPASS, cinco
contratos de arquitectura conservados/cero rotos y revisión ClaudeGO de los
bytes cuyos hashes Codex volvió a comprobar.

Composición: stdout/stderr nativos -> señales fijas -> categoría sinACK y
recibo seguro; el consumidor OpenCode también llama al helper compartido y
sus controles de diagnóstico/recibo pasan. Sin nueva API/DB/migración/estado
por agente ni esquema público; no hace falta Pact nuevo o dependencia real
para este cálculo puro. Mismo límite de Graphify registrado en T195 y fronteras
verificadas contra fuente; grafo ajeno preservado.

El fallback descriptivo sigue siendo heurístico: frases como unauthorized o
rate limit dentro de texto del modelo pueden seguir coincidiendo como antes.
Este cambio elimina los dos falsos positivos demostrados; no pretende resolver
toda atribución de errores. No acepta auth/cuota reales, nuevos perfiles, Work
ni gates globales. Las aceptaciones T190/T195 siguen vinculadas a sus fuentes.
