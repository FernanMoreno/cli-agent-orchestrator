> Historical source review/change record. It describes a bounded earlier state of this demo, not current acceptance. Compare every claim with `../CONTRACT.md` and the current code/tests.

# FRONTEND_FINAL_AUDIT

Auditoría del frontend existente (`frontend/app.js` e `frontend/index.html`), realizada tras la cancelación del turno anterior y la nueva tarea acotada. No se modificó ninguna implementación ni `notes.json`.

## Contrato confirmado en el código

| Requisito | Estado | Evidencia |
|---|---|---|
| Fetch relativo `api/notes` | Confirmado | `cargarNotas()` usa `fetch('api/notes')` (GET relativo). `agregarNota()` usa `fetch('api/notes', …)` y `alternarNota()` usa `fetch(\`api/notes/${id}\`, …)`, ambos relativos. |
| POST con cuerpo `{text}` | Confirmado | `agregarNota()` envía `method: 'POST'`, `Content-Type: application/json` y `body: JSON.stringify({ text: texto })`. |
| PATCH con cuerpo `{done}` | Confirmado | `alternarNota()` envía `method: 'PATCH'`, `Content-Type: application/json` y `body: JSON.stringify({ done })` sobre `api/notes/{id}`. |
| Render de checkbox | Confirmado | `renderizarNotas()` crea un `<input type="checkbox">` por nota, con `checked = nota.done`, clase `completada` en el `<li>` cuando aplica, y listener `change` que invoca `alternarNota(nota.id, checkbox.checked)`. |
| Recarga tras mutaciones | Confirmado | Tras POST exitoso se ejecuta `await cargarNotas()`; tras PATCH exitoso se ejecuta `await cargarNotas()`. La carga inicial también invoca `cargarNotas()` al final del script. |

## Observaciones

- El frontend consume el contrato `{id, text, done}` implícitamente: lee `nota.id`, `nota.text` y `nota.done` al renderizar, y envía `{ text }` y `{ done }` al mutar.
- No se detectaron llamadas a rutas absolutas ni endpoints distintos de `api/notes` y `api/notes/{id}`.
- `index.html` carga `app.js` mediante script relativo y provee los elementos `form-nota`, `input-nota` y `lista-notas` que el script utiliza.

## Conclusión

El frontend existente cumple el contrato solicitado: fetch relativo a `api/notes`, creación con `POST {text}`, actualización con `PATCH {done}`, renderizado con checkbox y recarga de la lista tras cada mutación. No se requirió ningún cambio de implementación.
