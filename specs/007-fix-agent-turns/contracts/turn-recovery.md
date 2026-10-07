# Turn recovery contracts

GET /terminals/{id}/turn: read/write/admin. Proyección pública con terminal_id, provider, generation, state, reason, attempts, allowed_actions. Nunca receipt_sha256 ni nonce. Sin turno: state=none.

POST /terminals/{id}/turn/verify y /turn/cancel: write/admin; generation obligatoria de 32 hex. Generación desfasada o acción incompatible: 409 tipado. Work-owned: 403; ownership inaccesible: 503. No tarea reenviada. Cancelación exige fence durable y parada de ejecución propia, deja evidencia de incertidumbre si parada falla.

GET /terminals/{id}/output?mode=last: respuesta verificada conserva output/mode; condición pendiente devuelve 202 con estado y turno; reconciliación/cancelación devuelve 409 con diagnóstico tipado. Captura/extracción/persistencia fallida devuelve 500, nunca 202 por excepción genérica. FULL conserva 200 y transcripción incluso con turno sin resultado; no proclama resultado verificado.

GET /terminals/{id}: agrega turn opcional. Status de reconciliación visible a todos los consumidores; waiting_user_answer y waiting_quota conservan semántica.

CLI cao agent status/result usan misma proyección. cao agent verify usa generación vigente; cancel integra contrato durable cuando hay turno con recibo, preserva interrupt heredado para proveedores sin recibo. MCP expone acciones equivalentes y conserva dict de diagnóstico sin convertir pending en success.

Web muestra estado/motivo y acciones permitidas con generación actual. Transcripción xterm se conserva y no se etiqueta como resultado final.
