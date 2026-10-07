# Data model

## TerminalTurnReceipt (existente)

Una fila por terminal, generación opaca de 32 hex y hash de recibo de 64 hex. Fases prepared, sent, result_verified. Claim previo a paste y CAS actual se conservan. No contiene prompt ni recibo en claro.

## TerminalTurnRecovery (aditivo)

Clave compuesta terminal_id/generation. Estado pendiente, verificando, reconciliación, verificado, cancelando o cancelado; intentos automáticos; instante de primera detección final; motivo público; resultado verificado opcional; timestamps. El resultado solo se guarda después de prueba válida de recibo y estado final. Histórico conserva generaciones anteriores sin incluir secretos en proyección pública.

La transición a verificado pertenece a misma transacción de settlement del recibo. Cancelando/cancelado impiden settlement tardío y claim nuevo hasta parada confirmada. Cancelado nunca cambia a verificado. La consulta no incrementa intentos ni entrega mensajes; verify reevalúa evidencia actual y no crea generación.

## NativeChild / Inbox (existentes)

Hijos pueden reflejar reconciliación/cancelación, pero no son dueños del turno. Inbox conserva ID, remitente, destinatario, secuencia y contenido. Bloqueo antes de paste mantiene pending; entrega incierta mantiene reconcile y no se reenvía automáticamente. Ninguna acción de consulta modifica inbox.

## Provider availability

Ruta absoluta ejecutable, PATH efectivo y perfil nativo legible/compatible. Se comprueban antes de tarea; fallos no dejan proveedor listo. No se almacenan credenciales en diagnóstico.
