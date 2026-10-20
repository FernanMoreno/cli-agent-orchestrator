# Contrato HTTP

Servidor Python estándar en `0.0.0.0:8092`. Los datos persisten en `notes.json` del directorio actual.

- `GET /api/health` responde `200` con `{"ok":true}`.
- `GET /api/notes` responde `200` con una lista de notas.
- `POST /api/notes` acepta `{"text":"..."}` y responde `201` con `{id,text,done:false}`. `text` debe ser una cadena; se recortan los espacios de los extremos antes de validar y guardar. Su longitud resultante debe estar entre 1 y 2000 caracteres; valores vacíos, no string o excesivos reciben `400`.
- `PATCH /api/notes/{id}` acepta `{"done":true|false}` y responde `200` con la nota actualizada.
- `DELETE /api/notes/{id}` responde `200` con `{"id":"...","deleted":true}`; si la nota no existe, responde `404`.
- Las operaciones de lectura-modificación-escritura se protegen con un lock dentro del proceso del servidor. El JSON se publica mediante reemplazo atómico de un archivo temporal en el mismo directorio, evitando pérdidas de actualizaciones entre solicitudes concurrentes.
- JSON inválido o campos no válidos reciben `400`; rutas/notas desconocidas reciben `404`.
- `GET /` entrega `frontend/index.html`; recursos con rutas relativas se sirven desde `frontend/` sin permitir escapar de ese directorio.
