# T178 · Fallo parcial Codex mediante API y recibo nativo

Aceptación acotada **PASS**, 2026-10-27, fuente `05f1c36907697543684ae2bc31a1827f6c3f6f12`, CLI0.164.0. [Recibo estructurado](validation-t178.json).

El CLI real recibe un stream HTTP200 con texto parcial y un evento de fallo, seguido de respuestas401 del backend sintético aislado. La API de producción devuelve502/error en **35.311s**, incertidumbre de entrega true y retryable false, con el mismo hijo failed. El join confirma identidad, padre y settled_at antes de limpiar; después del DELETE conserva el veredicto y añade cleanup_completed_at. No se reenvía el prompt: una generación de recibo, con reintentos HTTP propios del CLI intactos.

El observador opcional se ejecuta solo después de esos controles y puede vetar DELETE. GET del terminal confirma id/proveedor/caller/sesión; una única fila del socket privado coincide por opción @cao_terminal_id, sesión y ventana. Se comprueban dimensiones de producción220x50, se captura **una vez** el panel visible con capture-pane -p -S0, sin unir filas, y se reconfirma la vinculación. La pantalla contiene la respuesta con canario, fila vacía y401en el margen, sin Worked for. El SHA de la captura privada coincide con el recibo. No se lee FULL: es normalmente un buffer crudo y puede disparar verificación de recibos, por lo que no acredita el viewport.

Tras borrar solo el hijo, la API devuelve404, el join conserva estado/settled_at, el pane desaparece del mismo socket y el padre sigue accesible. Se demuestra salida del wrapper por identidad de nacimiento/pidfd. Finalmente terminan API, HOME temporal, tmux y procesos observados. Cero conexiones/bases privadas antes del cierre del propietarioPG; contenedor y red exactos ausentes. No se afirma inspección independiente del inode de namespace.

Preflight de perfil/configuración y TTY real:9,001s,220x50, texto parcial seguido de401. El backend realmente ejecutado y binario coinciden con los pins; 1729archivos de fuente y6entradas privadas quedan intactos. HOME del operador oculto, solo loopback, clave sintética y ningún login copiado. Plazo180s y límites de memoria6144MiB intactos. El recorrido completo con cierre de fixtures dura97.335s.

RED21 por observador ausente;85enfocadas pasan, regresión199PASS/0omisiones/0avisos en48,28s,3casos nuevos de veto y controles reforzados de orden/propiedad. El predicado de pantalla supera1positivo/6negativos. Arquitectura5conservados/0rotos y revisión READ Claude favorable. T178 solo amplía el diagnóstico; la corrección productiva pertenece a T177.

No acepta errores HTTP envueltos/ausencia de footer, cuota tras texto parcial, cancelación activa, timeout/reconciliación, auto-resume ni todoCX08/global. Es CLI/API reales contra errores sintéticos, no rechazo de una cuenta real ni aceptación trasladable a otros proveedores.

Obsidian actualizado mediante `save-20261027-caos-managed-partial-error`: cuatro páginas, lint estricto sin incidencias. Revisión final READ de resultado y ledgers ClaudeGO; pins/preflight/pruebas verificados por el responsable.
