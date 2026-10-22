# T079 — perfil de receptor compatible con el recibo obligatorio

2026-10-22 UTC. Estado: corrección del perfil revisada y ejecutada; recorrido real FAILED, sin aceptación. Fuente main8057fcc6,507 pins sin cambios.

El replay offline del viewport conservado de T077 usó get_status, extractor v2 y verificador de recibo reales. Estado COMPLETED, respuesta ACK de3 caracteres, recibo activo ausente en esa respuesta. El único token cuyo hash coincide aparece en el contrato eco. Es evidencia de respuesta sin recibo; el parser rechazó correctamente el resultado. No explica otros fallos históricos.

El perfil del lanzador decía «reply exactly ACK in plain text». Esa instrucción contradice la obligación de añadir el recibo del contrato de entrega. La nueva entrada distingue cuerpo y metadatos:

> Reply with the acknowledgement body ACK in plain text. Also append the active machine turn receipt required by the delivery contract as a separate final line. The receipt is protocol metadata, separate from the acknowledgement body. Do not use tools, ask questions, or send messages.

Los permisos del receptor siguen vacíos y los del emisor son idénticos. La revisión detectó que el recibo era estricto, pero receiver_ack_exact admitía False o None: el ACK exacto era solo diagnóstico. La corrección exige ahora True en ambas fases, rechaza un ACK ausente antes de continuar y mantiene el recibo activo independiente; no acepta el token del prompt. Tampoco cambian modelo,75s,6144MiB, mensajes/PG/AG-UI/reinicio, identidad durable ni limpieza. Cambian el cuerpo del perfil receptor y el gate de ACK, que se endurece; el resto de criterios se conserva. La comparación AST histórica sigue cubriendo la función completa después de normalizar únicamente esa cláusula a su forma más estricta; rechaza cláusulas ausentes o ambiguas.

Controles actuales:45 comprobaciones de contrato con el viewport real y recibos sintéticos; self-check203+5+8+10+13+35. Los controles no prueban que el modelo vaya a obedecer. La ejecución real y su recibo quedan pendientes de revisión y serialización tras T078.

La regresión RED reprodujo que receiver_ack_exact=False aprobaba antes. Tras la corrección, las25 combinaciones de ACK de ambas fases solo aprueban True/True; False,None,texto y0 fallan. Se conservaron el resto de controles y se añadió el caso None. Las afirmaciones históricas sobre ACK deben distinguir una observación de un gate obligatorio; no se reescriben los recibos anteriores.


Resultado real T079: FAILED en133,806s. Emisor Unicode/durable PASS13,58s; receptor agotó75s con receipt_result_unverifiable,3intentos y fase sent. No reinicio ni segunda fase. Limpieza propia completa, fuente y operador preservados. Se conservaron los bytes ejecutados y se cerró el lanzador. La entrada corregida no garantiza obediencia del modelo.

El replay de la captura sí encontró un defecto reproducible de extracción: una repetición incompleta del encabezado del contrato en texto posterior sustituía su frontera completa y provocaba respuesta vacía. T080 corrige esa selección. Tras corregirla, la captura conserva1221caracteres con recibo activo, pero el cuerpo no es ACK exacto: hay texto adicional y dos pares ACK/recibo sin frontera estructural de canal demostrada. Este replay sigue rechazado por el gate más estricto; no se elige el último ACK ni se elimina contenido ambiguo para aprobar.

[Entradas explícitas de perfiles para el diagnóstico](fixtures/native-message-acceptance-profiles.json). No son perfiles productivos autoactivados.
