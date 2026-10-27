# T177 · Error Codex después de texto parcial

Corrección y verificación acotadas **PASS**, 2026-10-27. [Recibo estructurado](validation-t177.json).

Una captura real CLI0.164.0 sobre fuente `5c646430` muestra `•` con texto parcial, una fila vacía y `■ unexpected status 401` en el margen izquierdo. El parser anterior devolvía COMPLETED sin recibo y PROCESSING con recibo activo: cuatro fallos reproducidos entre las rutas de buffer/pantalla. El backend local sintético emitió primero texto SSE y un fallo, seguido de respuestas401; los12POST produjeron un200 y once401. La lista del recibo conserva orden de terminación HTTP, no orden de inicio de intentos. Captura8,468s; memoria10473MiB, reintentos del CLI y plazo180s intactos.

El control correcto, en2,653s y10226MiB, muestra una cita del mismo error con sangría de dos espacios y `Worked for`. Se observó un stream200 terminado y un segundo POST iniciado antes de terminar el proceso; no se afirma que todos los POST acabaran. El primer control se detuvo prematuramente sobre el símbolo citado: INCOMPLETE conservado, nunca aceptación. El predicado corregido exige stream terminado y canario; sus controles AST rechazan el caso prematuro.

La corrección exige una respuesta `•` en el margen, separador vacío y una celda HTTP nativa conocida como última fila del turno actual. No amplía cualquier símbolo `■`, cuotas, errores anteriores ni citas adyacentes/indentadas. La pantalla calcula esa frontera sobre las filas originales y mantiene filas compactas para los detectores existentes de confianza/actualización/aprobación. El detector compartido conserva prioridad de estado nativo, shell y diálogos; la sonda de progreso usa ese mismo camino.

La revisión reprodujo otros4fallos: un diálogo con18filas vacías y entrada multilínea con viñeta indentada. Ambos se corrigieron y se conservan como controles. Final: 399PASS/3omisiones opt-in preexistentes/2avisos, 171.64s;64enfocadas,24casos nuevos, arquitectura5/0. Fuente consumidora `de3fc61f35ad1e423b621246fc07eca4fd25c216`; ocho clasificaciones finales sobre las dos capturas renderizadas, recibos intactos y fixtures vinculados por SHA256. Esto prueba ambos adaptadores con texto renderizado, no el flujo pipe-pane crudo nativo.

Los procesos PTY fueron terminados después de capturar y recogidos, el servidor local cerró su hilo y el proceso exterior salió0. Los tres lanzadores quedaron cerrados. HOME del operador oculto, clave sintética, loopback aislado y ningún login copiado. La captura no usa PostgreSQL; la regresión reutilizó un único propietario privado verificado, cero conexiones/bases antes de su cierre y contenedor/red exactos ausentes. Los HOME sintéticos de captura se conservan como evidencia privada. Revisión Claude/Luna con alcance indicado en el JSON.

Quedan abiertos el recorrido gestionado de este fallo en la fuente corregida, HTTP nativo envuelto en varias filas, ausencia de footer, cuota tras texto parcial, cancelación activa y timeout/reconciliación. No cierra CX08/global ni otros proveedores.

Los eventos SSE siguen las formas del [harness oficial de Codex](https://github.com/openai/codex/blob/main/sdk/python/tests/app_server_harness.py) y la [referencia Responses](https://developers.openai.com/api/reference/resources/responses/streaming-events). La compatibilidad y el render de0.164 se acreditan con las capturas, no por asumir equivalencia con la rama upstream actual.

Obsidian: transacción `save-20261027-caos-partial-native-error`, cuatro páginas actualizadas y lint estricto sin incidencias.
