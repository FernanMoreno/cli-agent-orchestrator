# Aceptación autónoma real: supervisor Claude, Codex y OpenCode

Fecha: 2026-10-02. Rama: `main`. **Resultado global: FAIL; no se acredita autonomía completa.**

Se reutilizó `/mnt/c/users/ferna/onedrive/escritorio/cao-turn-recovery-demo-20261002-f9086a25`. Cada caso recibió una única orden inicial dirigida a Claude. El supervisor debía asignar exactamente dos trabajadores nativos mediante CAO, intercambiar un contrato y su aceptación entre Codex y OpenCode, recibir resultados auténticos y verificar ambos antes de declarar finalización. El observador del operador leyó SQLite en modo de solo lectura. No envió instrucciones correctivas, reintentos ni solicitudes de verificación a los trabajadores durante los casos válidos. El límite fue de 20 minutos por caso.

La ejecución creó perfiles y servidores aislados con credenciales locales existentes. El controlador inyectó únicamente los fallos anunciados y realizó el cierre de las sesiones. Las modificaciones del demo proceden de los modelos reales. El operador no reparó el producto para conseguir un resultado verde.

## Resultados y límites

| Caso | Evidencia observada | Aceptación |
|---|---|---|
| Edición de notas, ejecución normal | Codex terminó; OpenCode produjo el recibo auténtico, pero CAO mantuvo `receipt_result_unverifiable`. Persistieron mensajes pendientes. El HTTP independiente de edición pasó 24 comprobaciones; 10 pruebas unitarias pasaron. | FAIL de autonomía |
| Estadísticas, primer intento de recibo tardío | Codex falló durante inicialización. OpenCode implementó la interfaz y emitió un recibo auténtico, pero quedó en reconciliación. `GET /api/stats` devolvió 404. La inyección no registró ningún testigo auténtico: este intento **no prueba** recuperación de recibos tardíos. | FAIL funcional; escenario de inyección no demostrado |
| Auditoría adicional de recibo auténtico tardío | La inyección se vinculó al terminal y generación exactos, rechazó solo una respuesta con testigo nativo auténtico, registró tres observaciones y mantuvo la indisponibilidad 65 segundos. Codex pasó a `succeeded` tras liberar la evidencia, sin nueva orden. OpenCode quedó en reconciliación. | Recuperación tardía de Codex PASS; conjunto FAIL |
| Reinicio del servidor CAO | El reinicio se ejecutó después de confirmar ambos trabajadores y sus turnos enviados. Conservó identidades y asignaciones. OpenCode terminó con `provider.quota`, HTTP 429, `Go usage limit exceeded`. | Cierre completo tras reinicio no demostrado; conservar identidades no equivale a terminar |
| Navegador Chromium real | El POST de una nota devolvió 201, pero la interfaz lanzó `btnBorrarCompletadas is not defined` y no actualizó la lista. | FAIL |
| Limpieza HTTP independiente | DELETE completed eliminó una completada, conservó la pendiente y devolvió cero al repetirse. GET stats continuó devolviendo 404. Datos de prueba separados del archivo original. | Limpieza backend PASS; estadísticas FAIL |

Los casos fueron secuenciales sobre el mismo demo: una mejora incompleta puede afectar a las siguientes. La auditoría adicional en paralelo fue de solo lectura del código y escritura de dos informes; no autorizó cambios de funcionalidad. La cuota externa impide atribuir por sí sola el fallo del reinicio al mecanismo de recuperación. La evidencia sí muestra trabajadores y mensajes sin cierre; no certifica recuperación integral.

## Primer límite incorrecto reproducido: extracción de OpenCode v2

La base nativa de OpenCode contiene respuestas finales `finish=stop` y una línea con el recibo exacto esperado, tanto en edición como en estadísticas. Las capturas inmutables del terminal también contienen esos recibos. No fue una omisión del modelo.

El replay del proveedor actual devuelve `completed`, pero `extract_last_message_from_script` selecciona el último marcador de duración y recorta la respuesta antes de él. En ambas capturas ese marcador corresponde a un bloque anterior que termina en `Transport: The operation timed out.`. La respuesta posterior con el recibo correcto termina con un pie que muestra tokens, proveedor y coste, sin duración. El resultado extraído incluye un recibo citado dentro de instrucciones, pero **ninguna línea independiente de recibo**. La verificación rechaza correctamente ese resultado; lo incorrecto es el bloque elegido para verificar.

Referencias: [gramática de duración](../../src/cli_agent_orchestrator/providers/opencode_cli.py) (línea histórica `64`), [detección de finalización](../../src/cli_agent_orchestrator/providers/opencode_cli.py) (línea histórica `504`), [selección del bloque](../../src/cli_agent_orchestrator/providers/opencode_cli.py) (línea histórica `650`). Los archivos `main-opencode-*-diagnosis.json`, `main-opencode-parser-replay.json` y `main-opencode-extracted-replay.json` conservan hashes, posiciones y resultados sin publicar el nonce.

Corrección pendiente: seleccionar la respuesta actual y su recibo correlacionado sin depender exclusivamente del pie de duración, conservando rechazo de recibos citados, generaciones anteriores, procesamiento activo y respuestas incompletas. Reproducir primero ambas capturas como regresiones y repetir después la aceptación autónoma real. Esta auditoría **no implementó esa corrección**.

La regresión del demo también tiene una causa concreta: `frontend/app.js` declara `btnLimpiarCompletadas` y usa `btnBorrarCompletadas` sin declarar en actualización de contadores, limpieza y registro del evento. La prueba de navegador detectó el fallo que `node --check` y las pruebas exclusivamente del API no detectan. El endpoint de estadísticas tampoco existe en el backend final observado.

## Incidentes del controlador, excluidos de aceptación

El primer controlador confundió una mención futura del marcador de finalización con una línea final efectiva y cerró sesiones antes de tiempo. Otro perfil heredaba restricciones de solo lectura a los trabajadores. Un primer reinicio se disparó durante inicialización. Estos intentos se clasifican como **INVALID**, no como éxito ni fallo del producto.

Se corrigieron el detector, los permisos iniciales de los perfiles y el momento del reinicio antes de los casos usados en la aceptación. El primer inyector de recibos tardíos era demasiado amplio; se añadió una ejecución independiente que exige un testigo auténtico, un terminal y una generación exactos. Estas correcciones del banco de pruebas son intervenciones del operador, registradas expresamente; no fueron instrucciones para rescatar a los modelos en un caso válido.

## Revisión de composición

Subsistemas implicados: perfil y arranque nativo; tmux/FIFO y captura; estado y generación del turno; extracción y verificación de recibos; buzón persistente; asignaciones y supervisor; reinicio y observación tardía; API y navegador del demo.

El control arquitectónico fresco pasó **5 contratos conservados, 0 rotos**. Ese resultado no cubre la semántica de la interfaz real de OpenCode ni la finalización autónoma: ambas requieren estas pruebas de composición. Se comprobaron dos intenciones de asignación distintas por caso, persistencia de identidad en el reinicio y conservación del archivo original de notas. Esto acredita ausencia de asignaciones duplicadas observadas, no una garantía universal de ejecución exactamente una vez.

No se modificó código de CAO durante esta verificación. El manifiesto de 384 archivos Python se compara con la versión integrada antes y después. No se hicieron commits, push ni operaciones sobre servidores personales. El controlador cierra únicamente sus propias sesiones y servidores.

## Evidencia reproducible

Directorio: evidence/2026-10-02-autonomous-acceptance (`evidence/2026-10-02-autonomous-acceptance`; artefacto histórico local).

Incluye controladores, órdenes originales, estados temporales y finales de turnos/asignaciones/buzones, resultados HTTP/unitarios/sintaxis, replay del parser, prueba Chromium, captura de pantalla, composición y comparación de hashes. `manifest.json` registra el SHA-256 original y la transformación aplicada. Los recibos y tokens se redactan; no se archivan credenciales ni bases nativas completas. El nombre privado `public-results.json` no implica que su contenido sea apto para publicar sin esta transformación.

El éxito anterior con un operador coordinando directamente tareas permanece como evidencia de ese escenario. **No demuestra el escenario más exigente de supervisor autónomo** y no debe usarse para afirmar que los tres proveedores funcionan perfectamente.

## Cierre observado

Las cuatro ejecuciones finales alcanzaron su límite sin certificar finalización conjunta. El caso de reinicio conservó las identidades pero terminó con tres mensajes pendientes y ambos hijos todavía `running` en la base de CAO. Ese estado no corresponde a una prueba de finalización; el runtime de OpenCode ya había registrado su error de cuota.

Las comprobaciones independientes de los tres snapshots terminaron: edición HTTP PASS; estadísticas y snapshot final HTTP FAIL por GET stats 404; pruebas unitarias 10/10, 10/10 y 12/12; sintaxis JavaScript PASS en los tres. El navegador real FAIL por variable sin declarar. Los hashes del código usado por el navegador coinciden con el demo final. La revisión arquitectónica PASS no cambia esos resultados funcionales.

Los controladores terminaron, los cuatro cierres de sesión devolvieron HTTP 200 y sus servidores fueron detenidos. Se cerró también el servidor aislado del navegador; su salida 143 corresponde al cierre deliberado. Se eliminaron las copias de autenticación del entorno de prueba y se comprobaron las tablas de credenciales de las bases privadas: ninguna conservaba filas. Las credenciales personales no se modificaron. `verification.json` confirma 384 archivos de CAO sin diferencias y SHA-256 del archivo original de notas conservado.

Trabajo restante concreto: regresión y corrección del extractor OpenCode v2; determinar por qué tras el reinicio permanecen turnos y mensajes pendientes pese a actividad nativa, incluyendo propagación del error de cuota; completar GET stats y corregir la variable del demo; repetir después los tres escenarios autónomos con cuota disponible y las verificaciones HTTP/Chromium. Ninguno de estos pendientes se considera resuelto por esta auditoría.

Los artefactos históricos locales mencionados en este informe se conservan fuera del conjunto publicado; no son evidencia de una ejecución nueva. Las comprobaciones actuales de cierre se registran en el spec 015.
