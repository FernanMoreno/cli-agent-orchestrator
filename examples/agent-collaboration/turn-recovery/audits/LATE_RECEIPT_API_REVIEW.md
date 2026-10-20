> Historical source review/change record. It describes a bounded earlier state of this demo, not current acceptance. Compare every claim with `../CONTRACT.md` and the current code/tests.

# Auditoría del API

Revisadas las fuentes actuales `api/server.py`, `test_api.py` y `CONTRACT.md`.

`PATCH /api/notes/{id}` acepta `text`, `done` o ambos. Recorta texto y valida 1–2000 caracteres; `done` requiere booleano. Devuelve 200 con `{id,text,done}`; conserva identidad y campos omitidos. Datos inválidos: 400 sin mutación. ID inexistente con cuerpo válido: 404. Campos desconocidos se ignoran si hay alguno reconocido.

No existe una ruta de estadísticas en el API actual; `GET /api/notes` devuelve la lista.

`DELETE /api/notes/completed` devuelve 200 con `{"deleted_count":N}`; elimina únicamente `done=true`, conserva pendientes y devuelve cero si no hay completadas.

Validación: 10 pruebas existentes aprobadas en 5,794 segundos, ejecutadas desde una copia temporal, con almacenamiento temporal y servidores HTTP reales en puertos libres. Código de salida: 0.

MD5 de `notes.json` antes y después: `74936d0b7a43a5d9df82c89e6a34d4af`. Sin cambios detectados durante las pruebas en los archivos vigilados. Sin modificaciones de implementación, contrato, pruebas ni frontend.

Coordinación: ID auto-web aún no recibido; PEER_CONTRACT y PEER_ACK pendientes para un turno posterior.
