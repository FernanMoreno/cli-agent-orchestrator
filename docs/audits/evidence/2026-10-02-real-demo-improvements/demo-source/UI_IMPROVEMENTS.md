# Mejoras de UI

## Controles añadidos
- **Búsqueda** (`#buscar-nota`): filtra por texto sin distinguir mayúsculas (`toLowerCase()` en término y nota), con `<label>` asociado.
- **Filtro** (`#filtro-notas`): Todas / Pendientes / Completadas, con `<label>` asociado.
- **Contadores** (`#contadores`): total de notas y pendientes, con `aria-live="polite"`.
- **Eliminar**: botón por nota con `aria-label`, que llama a `DELETE /api/notes/{id}`.
- **Errores** (`#error`): región visible con `role="alert"`; se limpia tras cada operación correcta. Ya no se usa `alert()`.

## Robustez
- El botón Añadir se deshabilita mientras el POST está pendiente (sin envíos duplicados) y el input solo se limpia tras una respuesta correcta.
- Checkbox y botón eliminar se deshabilitan durante su petición; el checkbox revierte su estado si el PATCH falla.

## Contrato respetado
- `GET /api/notes`, `POST /api/notes` (`{"text"}`), `PATCH /api/notes/{id}` (`{"done"}`), `DELETE /api/notes/{id}`.
- Fetch relativo (`api/notes`), `textContent`/`setAttribute` seguros (sin `innerHTML` con datos), diseño en español conservado.
- Sin cambios en backend, `notes.json` ni `test_api.py`.
