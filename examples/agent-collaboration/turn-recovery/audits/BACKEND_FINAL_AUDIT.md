> Historical source review/change record. It describes a bounded earlier state of this demo, not current acceptance. Compare every claim with `../CONTRACT.md` and the current code/tests.

# Auditoría final del backend

Auditoría estática y de solo lectura de `api/server.py` y `CONTRACT.md` (más lectura de `frontend/app.js` para confirmar las rutas usadas). No se hicieron solicitudes HTTP ni se modificaron código o datos.

## Hallazgos

- `GET /api/health`: confirmado en `Handler.do_GET`; responde `200` y `{"ok": true}`.
- `GET /api/notes`: confirmado; devuelve `load_notes()` como lista JSON con estado `200`.
- `POST /api/notes`: confirmado en `Handler.do_POST`; requiere un objeto JSON con `text` cadena no vacía, crea `{id, text, done: false}` y responde `201`. JSON ausente/inválido o campos incorrectos producen `400`.
- `PATCH /api/notes/{id}`: confirmado en `Handler.do_PATCH` mediante coincidencia completa de la ruta. Requiere `done` booleano; actualiza y devuelve la nota con `200`, responde `400` si el campo no es válido y `404` si la nota o ruta no existe. El contrato coincide con esta ruta con parámetro `{id}`.
- Servido de `/app.js`: confirmado. `do_GET` quita la barra inicial y concatena la ruta bajo `frontend/`; resuelve el destino y comprueba que permanezca dentro de ese directorio. Para `.js` responde `application/javascript; charset=utf-8`. `frontend/app.js` solicita `api/notes` y `api/notes/${id}` mediante URLs relativas, coherentes con el servido bajo la raíz.

## Alcance

La confirmación es de lectura de fuente, no una prueba de ejecución del servidor. El contrato describe persistencia en `notes.json` relativo al directorio de ejecución; este archivo no se leyó ni modificó.
