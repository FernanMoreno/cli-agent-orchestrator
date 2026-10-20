> Historical source review/change record. It describes a bounded earlier state of this demo, not current acceptance. Compare every claim with `../CONTRACT.md` and the current code/tests.

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

## Edición de notas (2026-10-02)
- **Editar**: botón real por nota con `aria-label` que incluye el texto (`Editar nota "…"`). Cambia el `<li>` a modo edición con un `<textarea maxlength="2000">` que conserva el texto actual y botones **Guardar** y **Cancelar** (botones reales, `aria-label` con el texto de la nota).
- **Foco gestionado**: al pulsar Editar el foco va al textarea (cursor al final); Guardar (éxito o sin cambios) y Cancelar devuelven el foco al botón Editar de esa nota.
- **Teclado**: Enter guarda (Shift+Enter inserta salto de línea), Escape cancela.
- **Validación cliente**: el texto recortado debe tener 1–2000 caracteres; si es inválido no se envía y el motivo aparece en una región `role="alert"` junto al campo (con `aria-invalid` y `aria-describedby`). El error se limpia al seguir escribiendo.
- **Guardado**: `PATCH /api/notes/{id}` con `{"text": recortado}` (solo `text`; nunca se envía `done`). Con el 200 se actualiza la nota local desde la respuesta `{id,text,done}` (id y done conservados). Los errores 400/404/red mantienen el modo edición con el texto intacto, el mensaje en la región de alerta y el foco en el textarea.
- **Borrador**: el texto en edición sobrevive a re-renderizados ajenos (p. ej. marcar otra nota); solo se descarta si la nota deja de ser visible o se borra. Una sola nota en edición a la vez.
- Se mantiene `createElement`/`textContent`/`.value`; nunca `innerHTML` con texto de usuario.

## Borrar completadas (2026-10-02)
- Botón `#btn-borrar-completadas` que llama a `DELETE /api/notes/completed` (200 `{"deleted_count":N}`).
- `aria-label` dinámico con el número de completadas; deshabilitado cuando no hay ninguna o mientras la petición está en curso. Los errores van a la región global `role="alert"`.

## Contadores desde /api/stats (2026-10-02)
- Tres contadores con ids estables: `#stat-total`, `#stat-pending` y `#stat-completed`, dentro de `#contadores` (mismo estilo `.contadores`, `aria-live="polite"`, textos en español).
- Fuente principal: `GET /api/stats` → `{"total":N,"pending":N,"completed":N}`; solo se aceptan enteros no negativos (`Number.isInteger`).
- `cargarStats()` se refresca tras: carga inicial, añadir, editar (Guardar), marcar/desmarcar, eliminar y borrar completadas (estas cinco últimas pasan por `cargarNotas()`; la edición llama a `cargarStats()` directamente).
- Manejo de error: ante fallo de red, HTTP no OK, JSON inválido o forma inesperada, los contadores caen al cálculo local sobre `notas` sin romper ni bloquear la lista; el botón Borrar completadas mantiene su estado y `aria-label`.
- Se sigue usando `textContent` (contenido escapado por construcción); nunca `innerHTML` con datos.
