# Modelo de datos y estados

Reutilizar entidades y SQLite existentes; no se propone migración.

| Entidad | Datos | Propietario e invariantes |
|---|---|---|
| Terminal | id, sesión, proveedor, perfil, caller_id, directorio, estado | terminal/session services; identidad propia, caller conservado, activo solo con proceso real |
| Delegación | trabajador, llamador, mensaje, modalidad, resultado/job_id | utilidades y resultado durable; timeout no equivale a cancelación |
| InboxMessage | id, sender_id, receiver_id, message, status, created_at | SQLite/inbox; locks por receptor, entrega no implica finalización |
| Proyecto | source, target, readonly | manifiesto privado; rutas exactas y permisos RO/RW |
| Integración | endpoint/comando, entorno privado, comprobación | config proveedor existente; secretos fuera de imagen/evidencia |
| Ejecución de aceptación | run_id, root privado, carpeta Escritorio, sesiones propias, puertos, resultados | runner; limpieza solo de recursos propios |

Transiciones:

- Terminal: inicialización → disponible/procesando → completado/fallo/cierre; reconciliar si desaparece el proceso, sin introducir estados incompatibles sin contrato.
- Mensaje: `pending` → `delivered`; rechazo previo recuperable conserva `pending`; definitivo `failed`; posible paste sin confirmación `reconcile`, sin reencolado automático.
- Delegación: aceptación → ejecución → resultado/fallo; timeout conserva referencia recuperable y no repite efectos.
- Prueba: prerrequisitos → arranque → escenarios → verificación → limpieza → resumen. Conservar proyecto; un escenario ausente queda pendiente.

Ninguna fila de terminal almacena contraseña/bearer. Identidad de coordinación y autoridad compartida son conceptos distintos.
