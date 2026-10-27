# T175 — errores HTTP y cuota en el Codex nativo

2026-10-27. **PASS acotado** del productor nativo y del clasificador de CAOS. Clasificador final en `80525802f77f6aa1f3f59f3bfa0ecd5d06b6a8c8`; capturas originales independientes en `f0a9587dbcc73d36a6831181bd7bb60439c1fe80`, CLI0.164.0 y binario fijado. [Recibo estructurado](validation-t175.json). No es aceptación completa de CX-08.

## Fallo reproducido y corrección

El CLI real dibuja errores HTTP con `■`, no con el prefijo antiguo `Error:`. El clasificador de CAOS devolvía IDLE sin recibo activo o PROCESSING con recibo pendiente para el401 capturado. La cuota real del protocolo usa además `You’ve` con apóstrofo curvo, que la expresión antigua no reconocía. RED:25fallos/7controles correctos.

Se reconocen únicamente las familias nativas observadas `■ unexpected status [45]dd` y `■ exceeded retry limit, last status: [45]dd`, conservando Error/Traceback/panic. El glifo solo no significa error: avisos informativos o de interrupción mantienen su comportamiento previo. Las frases completas de capacidad admiten ambas formas del apóstrofo; un429 genérico sigue siendo ERROR, no WAITING_QUOTA. Una cuota anterior al último turno real ya no bloquea el nuevo turno. Se conservan las guardas de respuesta del asistente y sugerencia del pie; CRLF/CR se normalizan antes de comparar posiciones.

La revisión señaló el riesgo de interpretar cualquier `■` como error. Cuatro controles nuevos fallaron con esa ampliación inicial y pasan al restringir las familias. Los avisos de interrupción de esos controles son sintéticos: no acreditan cancelación nativa. Los40controles finales verifican buffer/pantalla, contexto con/sin usuario, recibo pendiente, citas del asistente, cuotas antiguas y finales de línea. No se modifican preguntas, recibos, permisos, tiempos de espera ni límites de reintento del cliente.

## Capturas nativas controladas

| Caso | HTTP servido | Respuestas servidas | Captura y cierre, s | Estado final del parser |
| --- | ---: | ---: | ---: | --- |
| auth | 401 | 12 | 7.865 | error |
| throttle | 429 | 1 | 1.386 | error |
| quota | 429 | 1 | 1.189 | waiting_quota |

Se ejecutó el CLI nativo interactivo contra un servidor HTTP sintético en loopback. Cada caso usa HOME/CODEX_HOME vacíos, clave sintética y un perfil separado con lectura/approval never. bwrap crea namespaces de red/PID, oculta `/home/felni` completo y expone únicamente el runtime Python, binario fijado y directorio privado del caso; la única interfaz habilitada es loopback. No hay login copiado ni cuenta/modelo real accesibles. Apps/plugins/shell están desactivados solo en la configuración diagnóstica. No se reducen los reintentos nativos; la tabla registra las respuestas realmente emitidas.

La carga de la configuración se validó antes de abrir el PTY mediante `--profile error-safe mcp list --json`. Las capturas usan un terminal160×55 y decodificador UTF-8 incremental estricto. Los fixtures publicados conservan la pantalla relevante completa, retirando filas vacías/relleno derecho y sustituyendo únicamente el puerto de loopback por1. Los hashes del generador realmente ejecutado y su derivación cerrada se verificaron; todos los descriptores quedan cerrados. Los tiempos de la tabla comienzan al abrir el PTY e incluyen su cierre/servidor, no el preflight previo.

El primer intentoauth-r1 dejó texto en el editor y emitió cero peticiones durante180,232s con cierre: fallo de entrega de la sonda, conservado sin aceptarlo como rechazo HTTP. La corrección usa el pegado delimitado, la observación del texto y el Enter separado de producción, con su demora0,3s. La admisión también conservó fallos de capacidades/montaje antes de arrancar el CLI; el namespace definitivo fue probado con loopback/Python/pyte y HOME del operador oculto.

## Validación y límites de composición

La fuente final consumió las tres pantallas fuente-vinculadas sin regenerarlas ni relabelar su revisión:12clasificaciones correctas (buffer/pantalla × sin/con recibo marcado enviado), sin modificar el recibo. Se fijaron **1724 archivos** del consumidor y se vincularon exactamente los fixtures a sus capturas. Productor CLI y consumidor CAOS son observaciones distintas; esta unión no sustituye un recorrido gestionado por API.

Suite final: **416PASS,3omisiones opt-in preexistentes y4avisos,183,28s**, dos workers con PostgreSQL privado verificado/autostart desactivado. Las omisiones son las comprobaciones nativas de ajuste de actualización, clave de configuración inválida y flags; no se habilitaron ni se cuentan como aceptadas. Los40casos nuevos no tienen omisiones. Una suite anterior408PASS/3omisiones/334,53s usó la expresión inicial más amplia y conserva ese alcance; la suite final se repitió después de la corrección de revisión. Black/isort y arquitectura5/0 pasan.

Revisión de composición PASS para productor HTTP→CLI/PTY→fixture exacto→clasificador de turno y controles del bucle agent_step. PostgreSQL se usó para las fixtures completas, no para simular la respuesta del CLI. Servidores HTTP y grupos de procesos propios reaped/cerrados, namespaces terminados; códigos nativos-15 reflejan cierre intencional de la interfaz interactiva después de observar el error, no salida natural del modelo. PostgreSQL: cero prestatarios/bases privadas antes de cerrar; contenedor/red exactos ausentes, contexto PostgreSQL devuelto. No se tocan recursos ajenos ni login del operador. Claude finalGO tras resolver su hallazgo; Luna revisó la sonda y la evidencia final con GO; se retiró la afirmación de código de salida del propietario porque el recibo estructurado solo acredita contexto devuelto y recursos ausentes.

T175 cierra esta clasificación acotada. Siguen pendientes el recorrido API/recibo para errores/cuota, errores después de una respuesta parcial del asistente, cancelación activa y reconciliación de timeout; no se acreditan agotamiento de cuenta real, otros proveedores, T032 ni gates globales. T173/T174/CX-06/CX-07 mantienen sus recibos y fuentes.
